"""Private throwaway services for scalability_load.py, configured like deploy/local.

``up`` starts PostgreSQL, Elasticsearch, Redis and Kafka containers named
``<prefix>-<service>`` on loopback ports, then proves readiness: a PostgreSQL
query, yellow cluster health, a Redis PING, and on Kafka an idempotent produce
plus a full transactional init/produce/commit. The generated password and admin
URL are written to ignored ``.local/<prefix>-postgres-password`` and
``.local/<prefix>-admin-url``, never printed or passed on a command line.
``down`` removes only these containers, their anonymous volumes and those files.
Never use host-wide prune commands.

    python qualification/scalability_services.py up
    python qualification/scalability_services.py down
"""

import argparse
import json
from pathlib import Path
import secrets
import socket
import subprocess
from time import monotonic, sleep
import urllib.request
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
IMAGES = {
    "postgres": "postgres:18.2",
    "elasticsearch": "docker.elastic.co/elasticsearch/elasticsearch:9.0.0",
    "redis": "redis:7.4.2",
    "kafka": "apache/kafka:4.0.0",
}


def docker(*command, check=True):
    completed = subprocess.run(["docker", *command], capture_output=True, text=True)
    if check and completed.returncode:
        # Report the subcommand and Docker's message, never the argument list.
        raise SystemExit(
            f"docker {command[0]} failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    return completed


def containers(prefix):
    return [f"{prefix}-{service}" for service in IMAGES]


def up(args):
    existing = docker("ps", "-a", "--format", "{{.Names}}").stdout.split()
    taken = [name for name in containers(args.prefix) if name in existing]
    if taken:
        raise SystemExit(f"Containers already exist; inspect or remove them first: {taken}")
    password = secrets.token_urlsafe(24)
    # As in compose, the server reads the password from a mounted file, so it is
    # absent from the command line, process listings and docker inspect.
    password_file = ROOT / ".local" / f"{args.prefix}-postgres-password"
    password_file.parent.mkdir(exist_ok=True)
    password_file.write_text(password)
    # Loopback only, as in deploy/local/compose.yaml; no named volumes.
    runs = {
        "postgres": [
            "--init",
            "-e",
            "POSTGRES_DB=keep",
            "-e",
            "POSTGRES_USER=keep_migrator",
            "-e",
            "POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password",
            "--mount",
            f"type=bind,source={password_file},target=/run/secrets/postgres_password,readonly",
            "-p",
            f"127.0.0.1:{args.postgres_port}:5432",
        ],
        "elasticsearch": [
            "-e",
            "discovery.type=single-node",
            "-e",
            "xpack.security.enabled=false",
            "-e",
            "ES_JAVA_OPTS=-Xms512m -Xmx512m",
            "-p",
            f"127.0.0.1:{args.elasticsearch_port}:9200",
        ],
        "redis": ["-p", f"127.0.0.1:{args.redis_port}:6379"],
        "kafka": [
            "--hostname",
            "kafka",
            "-p",
            f"127.0.0.1:{args.kafka_port}:9092",
            *(
                item
                for key, value in {
                    "KAFKA_NODE_ID": "1",
                    "KAFKA_PROCESS_ROLES": "broker,controller",
                    "KAFKA_LISTENERS": "PLAINTEXT://:9092,INTERNAL://:29092,CONTROLLER://:9093",
                    "KAFKA_ADVERTISED_LISTENERS": (
                        f"PLAINTEXT://127.0.0.1:{args.kafka_port},INTERNAL://kafka:29092"
                    ),
                    "KAFKA_CONTROLLER_LISTENER_NAMES": "CONTROLLER",
                    "KAFKA_LISTENER_SECURITY_PROTOCOL_MAP": (
                        "CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT,INTERNAL:PLAINTEXT"
                    ),
                    "KAFKA_INTER_BROKER_LISTENER_NAME": "INTERNAL",
                    "KAFKA_CONTROLLER_QUORUM_VOTERS": "1@localhost:9093",
                    "KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR": "1",
                    "KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR": "1",
                    "KAFKA_TRANSACTION_STATE_LOG_MIN_ISR": "1",
                }.items()
                for item in ("-e", f"{key}={value}")
            ),
        ],
    }
    for service, options in runs.items():
        docker("run", "-d", "--name", f"{args.prefix}-{service}", *options, IMAGES[service])
    admin = f"postgresql://keep_migrator:{password}@127.0.0.1:{args.postgres_port}/keep"
    state = ROOT / ".local" / f"{args.prefix}-admin-url"
    state.parent.mkdir(exist_ok=True)
    state.write_text(admin)
    ready(args, admin)
    print(
        json.dumps(
            {
                "containers": containers(args.prefix),
                "images": IMAGES,
                "admin_url_file": str(state),
                "kafka_bootstrap": f"127.0.0.1:{args.kafka_port}",
                "elasticsearch_url": f"http://127.0.0.1:{args.elasticsearch_port}",
                "redis_url": f"redis://127.0.0.1:{args.redis_port}/0",
                "ready": True,
            },
            indent=2,
        )
    )


def wait(label, probe, seconds=180):
    deadline, failure = monotonic() + seconds, None
    while monotonic() < deadline:
        try:
            if probe():
                print(f"{label} ready", flush=True)
                return
        except Exception as error:
            failure = type(error).__name__
        sleep(1)
    raise SystemExit(f"{label} not ready within {seconds}s ({failure})")


def ready(args, admin):
    import psycopg
    from confluent_kafka import Producer
    from confluent_kafka.admin import AdminClient, NewTopic

    def postgres():
        with psycopg.connect(admin, connect_timeout=3) as conn:
            return conn.execute("SELECT 1").fetchone()[0] == 1

    def elasticsearch():
        url = f"http://127.0.0.1:{args.elasticsearch_port}/_cluster/health?wait_for_status=yellow&timeout=5s"
        with urllib.request.urlopen(url, timeout=8) as response:
            return json.loads(response.read())["status"] in ("yellow", "green")

    def redis():
        with socket.create_connection(("127.0.0.1", args.redis_port), timeout=3) as sock:
            sock.sendall(b"PING\r\n")
            return sock.recv(16).startswith(b"+PONG")

    bootstrap = f"127.0.0.1:{args.kafka_port}"
    topic = "keep-load-readiness-" + uuid4().hex

    def kafka_topic():
        admin_client = AdminClient({"bootstrap.servers": bootstrap})
        admin_client.create_topics([NewTopic(topic, 1, 1)])[topic].result(10)
        return True

    def kafka_idempotent():
        producer = Producer(
            {"bootstrap.servers": bootstrap, "enable.idempotence": True, "acks": "all"}
        )
        delivered = []
        producer.produce(topic, b"idempotent", on_delivery=lambda e, m: delivered.append(e))
        producer.flush(10)
        return delivered == [None]

    def kafka_transaction():
        producer = Producer(
            {
                "bootstrap.servers": bootstrap,
                "transactional.id": topic,
                "enable.idempotence": True,
                "acks": "all",
            }
        )
        producer.init_transactions(15)
        producer.begin_transaction()
        producer.produce(topic, b"transactional")
        producer.commit_transaction(15)
        return True

    wait("PostgreSQL", postgres)
    wait("Elasticsearch", elasticsearch, 300)
    wait("Redis", redis)
    wait("Kafka topic creation", kafka_topic)
    wait("Kafka idempotent produce", kafka_idempotent)
    wait("Kafka transactional produce", kafka_transaction)
    # Keep the client referenced until the deletion completes.
    admin_client = AdminClient({"bootstrap.servers": bootstrap})
    admin_client.delete_topics([topic])[topic].result(10)


def down(args):
    names = containers(args.prefix)
    existing = set(docker("ps", "-a", "--format", "{{.Names}}").stdout.split())
    present = [name for name in names if name in existing]
    if present:
        docker("rm", "-f", "-v", *present)
    (ROOT / ".local" / f"{args.prefix}-admin-url").unlink(missing_ok=True)
    (ROOT / ".local" / f"{args.prefix}-postgres-password").unlink(missing_ok=True)
    print(json.dumps({"removed": present}))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("up", "down"))
    parser.add_argument("--prefix", default="keep-load")
    parser.add_argument("--postgres-port", type=int, default=55633)
    parser.add_argument("--elasticsearch-port", type=int, default=59401)
    parser.add_argument("--redis-port", type=int, default=56580)
    parser.add_argument("--kafka-port", type=int, default=19293)
    args = parser.parse_args()
    if not args.prefix.startswith("keep-load"):
        parser.error("Use an keep-load prefix so owned containers stay identifiable")
    (up if args.action == "up" else down)(args)


if __name__ == "__main__":
    main()
