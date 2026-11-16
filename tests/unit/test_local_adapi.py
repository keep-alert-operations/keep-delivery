"""The mock ADAPI copies the user-stated ADAPI contract that later Keep tests rely on.

Requirements: REV-73; plan A9, A11, A13, A19-A22, A25-A27 (fixture for them).
"""

import io
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import local_adapi  # noqa: E402
from local_adapi import FilterError, escape_filter_value, parse_filter  # noqa: E402

CLIENT_ID = "unit-client-id"
# One plain-HTTP client: building an SSL context per call costs ~0.5 s on Windows.
HTTP = httpx.Client(timeout=10, verify=False, trust_env=False)


@pytest.fixture
def start():
    servers = []

    def run(directory=None, **options):
        options.setdefault("client_id", CLIENT_ID)
        server = local_adapi.LocalADAPI(directory, **options).start()
        servers.append(server)
        return server

    yield run
    for server in servers:
        server.stop()


@pytest.fixture
def adapi(start):
    return start()


def get(server, path, params=None, client_id=CLIENT_ID, **kwargs):
    headers = {} if client_id is None else {"ClientId": client_id}
    return HTTP.get(server.base_url + path, params=params, headers=headers, **kwargs)


def names(response):
    assert response.status_code == 200, response.text
    return [row["sAMAccountName"] for row in response.json()]


def test_exact_user_lookup_is_case_insensitive_and_has_the_stated_shape(adapi):
    """Requirements: REV-73; plan Directory protocol (exact lookup), A13."""
    rows = get(adapi, "/users", {"samAccountName": "LOCAL-ADMIN"}).json()
    assert len(rows) == 1
    row = rows[0]
    assert list(row) == list(local_adapi.USER_ATTRIBUTES)
    assert row["sAMAccountName"] == "local-admin"
    assert row["displayName"] == "Local Administrator"
    assert row["userAccountControl"] == "512"
    assert all(isinstance(v, str) for k, v in row.items() if k != "memberOf")
    assert row["memberOf"] == ["CN=Keep Administrators,OU=Groups,DC=keep,DC=local"]
    disabled = get(adapi, "/users", {"samAccountName": "local-disabled"}).json()
    assert disabled[0]["userAccountControl"] == "514"


def test_groups_use_their_own_casing_and_hide_display_name_unless_asked(adapi):
    """Requirements: REV-73; plan Verified facts (ADAPI response shapes)."""
    row = get(adapi, "/groups", {"samAccountName": "keep-developers"}).json()[0]
    assert list(row) == list(local_adapi.GROUP_ATTRIBUTES)
    assert "displayName" not in row and "objectGuid" in row and "mailNickName" in row
    assert row["groupType"] == local_adapi.SECURITY_GROUP
    chosen = get(
        adapi, "/groups", {"samAccountName": "keep-developers", "attributesToLoad": "displayName"}
    ).json()
    assert chosen == [{"displayName": "Developers", "sAMAccountName": "keep-developers"}]
    plain = get(
        adapi, "/groups", {"samAccountName": "keep-dev-announce", "attributesToLoad": "displayName"}
    ).json()
    assert plain == [{"sAMAccountName": "keep-dev-announce"}]
    assert get(adapi, "/groups", {"samAccountName": "keep-dev-announce"}).json()[0][
        "groupType"
    ] == local_adapi.DISTRIBUTION_GROUP


def test_wildcards_and_or_ed_parameters(adapi):
    """Requirements: REV-73; plan Decisions (picker search params), A11."""
    assert names(get(adapi, "/users", {"samAccountName": "*EDIT*"})) == ["local-editor"]
    assert names(get(adapi, "/users", {"samAccountName": "local-v*"})) == ["local-viewer"]
    assert names(get(adapi, "/users", {"samAccountName": "*admin"})) == ["local-admin"]
    assert names(get(adapi, "/users", {"samAccountName": "local"})) == []
    both = get(
        adapi, "/users", {"samAccountName": "*admin*", "customFilter": "(displayName=*viewer*)"}
    )
    assert names(both) == ["local-admin", "local-viewer"]
    hebrew = get(adapi, "/users", {"customFilter": "(displayName=*משתמש*)"})
    assert names(hebrew) == ["local-hebrew"]
    assert names(get(adapi, "/users", {"aNR": "Local Ed"})) == ["local-editor"]
    assert names(get(adapi, "/users", {"commonName": "local viewer"})) == ["local-viewer"]
    assert len(names(get(adapi, "/users", {"samAccountName": ""}))) == 7  # null = no filter


def test_custom_filter_batch_of_fifty_escaped_names(start):
    """Requirements: REV-73; plan Directory protocol (batched lookups), A11, A22."""
    data = local_adapi.build_directory(users=60, groups=0)
    data["users"].append({"sAMAccountName": "a(b)", "displayName": "Paren"})
    server = start(data)
    wanted = [f"user{i:05d}" for i in range(1, 49)] + ["a(b)", "x)(objectClass=*"]
    assert len(wanted) == 50
    clauses = "".join(f"(sAMAccountName={escape_filter_value(n)})" for n in wanted)
    response = get(
        server,
        "/users",
        {"customFilter": f"(|{clauses})", "attributesToLoad": "sAMAccountName,displayName,userAccountControl"},
    )
    rows = response.json()
    assert [r["sAMAccountName"] for r in rows] == wanted[:49]
    assert all(set(r) == {"sAMAccountName", "displayName", "userAccountControl"} for r in rows)
    assert escape_filter_value("x)(objectClass=*") == "x\\29\\28objectClass=\\2a"
    unescaped = "(|(sAMAccountName=x)(objectClass=*))"
    assert len(names(get(server, "/users", {"customFilter": unescaped}))) == 61
    injected = get(server, "/users", {"customFilter": "(sAMAccountName=x)(objectClass=*)"})
    assert injected.status_code == 400


def test_filter_parser_handles_rfc_4515_escapes_and_operators():
    """Requirements: REV-73; plan Decisions (escape ( ) * \\ in customFilter)."""
    row = {"sAMAccountName": "a*b", "displayName": "Back\\slash (x)", "memberOf": ["CN=G"]}
    assert local_adapi.matches(parse_filter("(sAMAccountName=a\\2ab)"), row)
    assert not local_adapi.matches(parse_filter("(sAMAccountName=a\\2a)"), row)
    assert local_adapi.matches(parse_filter("(displayname=*\\5cslash \\28x\\29)"), row)
    assert local_adapi.matches(parse_filter("(&(memberOf=cn=g)(!(sAMAccountName=z)))"), row)
    assert local_adapi.matches(parse_filter("(|(sAMAccountName=z)(displayName=back*x*))"), row)
    assert local_adapi.matches(parse_filter("(displayName=*)"), row)
    assert not local_adapi.matches(parse_filter("(mail=*)"), row)
    assert local_adapi.matches(parse_filter("sAMAccountName=A\\2AB"), row)
    hebrew = escape_filter_value("שלום")
    assert local_adapi.matches(parse_filter(f"(cn={hebrew})"), {"cn": "שלום"})
    utf8_hex = "".join(f"\\{b:02x}" for b in "שלום".encode())
    assert local_adapi.matches(parse_filter(f"(cn={utf8_hex})"), {"cn": "שלום"})
    for bad in ("", "(", "(cn=a", "cn=a)", "(cn=a)(cn=b)", "(|)", "(=a)", "(cn~=a)",
                "(cn>=a)", "(cn=\\2)", "(cn=\\zz)", "(cn=a(b)", "(cn=a**b)", "(cn=\\ff)"):
        with pytest.raises(FilterError):
            parse_filter(bad)


def test_malformed_custom_filter_answers_400(adapi):
    """Requirements: REV-73; plan Delivery and design (mock copies error statuses)."""
    response = get(adapi, "/users", {"customFilter": "(|(sAMAccountName=a)"})
    assert response.status_code == 400
    assert get(adapi, "/groups", {"customFilter": "(cn=a))"}).status_code == 400


def test_attributes_to_load_accepts_repeated_and_comma_separated(adapi):
    """Requirements: REV-73; plan Directory protocol (attributesToLoad)."""
    repeated = get(
        adapi, "/users", [("samAccountName", "local-admin"), ("attributesToLoad", "displayName"),
                          ("attributesToLoad", "userAccountControl")],
    ).json()
    comma = get(
        adapi, "/users",
        {"samAccountName": "local-admin", "attributesToLoad": "displayName,userAccountControl"},
    ).json()
    expected = [{"displayName": "Local Administrator", "sAMAccountName": "local-admin",
                 "userAccountControl": "512"}]
    assert repeated == comma == expected


def test_searches_cap_silently_at_one_thousand(start):
    """Requirements: REV-73; plan Decisions (1,000 results = type more), A14 fixture."""
    server = start(local_adapi.build_directory(users=1200, groups=0))
    assert len(names(get(server, "/users"))) == 1000
    assert len(names(get(server, "/users", {"samAccountName": "user*"}))) == 1000
    server.set_search_cap(5)
    assert len(names(get(server, "/users", {"samAccountName": "user*"}))) == 5


def test_client_id_is_required(adapi):
    """Requirements: REV-73; plan Decisions (ClientId header, failures non-200)."""
    for client_id in (None, "wrong", CLIENT_ID + "x"):
        for path in ("/users", "/groups", "/groups/members/user?samAccountName=keep-developers"):
            response = get(adapi, path, client_id=client_id)
            assert response.status_code == 401
            assert CLIENT_ID not in response.text
    assert get(adapi, "/nope").status_code == 404
    assert get(adapi, "/nope", client_id=None).status_code == 401


def test_injected_failures_by_route_name_and_count(adapi):
    """Requirements: REV-73; plan A5, A25, A26 (6) fixture."""
    adapi.fail("users", 503, name="local-viewer", times=1)
    assert get(adapi, "/users", {"samAccountName": "local-admin"}).status_code == 200
    assert get(adapi, "/users", {"samAccountName": "LOCAL-VIEWER"}).status_code == 503
    assert get(adapi, "/users", {"samAccountName": "local-viewer"}).status_code == 200
    batch = "(|(sAMAccountName=local-admin)(sAMAccountName=local-viewer))"
    adapi.fail("users", 500, name="local-viewer")
    assert get(adapi, "/users", {"customFilter": batch}).status_code == 500
    adapi.fail("members", 500, name="keep-developers")
    members = "/groups/members/user"
    assert get(adapi, members, {"samAccountName": "keep-developers"}).status_code == 500
    assert get(adapi, members, {"samAccountName": "keep-observers"}).status_code == 200
    adapi.clear_failures()
    adapi.fail("*", 500)
    assert get(adapi, "/groups").status_code == 500
    adapi.clear_failures()
    assert get(adapi, "/groups").status_code == 200


def test_recursive_members_follow_distribution_groups(adapi):
    """Requirements: REV-73; plan Decisions (recursive members route), A12, A22."""
    response = get(adapi, "/groups/members/user",
                   {"samAccountName": "KEEP-DEVELOPERS", "isRecursive": "true"})
    rows = response.json()
    assert [r["sAMAccountName"] for r in rows] == [
        "local-editor", "local-hebrew", "WS-0001$", "local-distribution", "local-disabled",
    ]
    assert all(list(r) == list(local_adapi.MEMBER_ATTRIBUTES) for r in rows)
    assert {r["sAMAccountName"]: r["userAccountControl"] for r in rows}["local-disabled"] == "514"
    direct = get(adapi, "/groups/members/user",
                 {"samAccountName": "keep-developers", "isRecursive": "false"})
    assert names(direct) == ["local-editor", "local-hebrew", "WS-0001$"]


def test_recursive_members_handle_cycles_and_have_no_cap(start):
    """Requirements: REV-73; plan A22, A26 (7), A27 fixture."""
    server = start(local_adapi.large_group("big-team", members=1500, distribution_members=100))
    rows = get(server, "/groups/members/user", {"samAccountName": "big-team", "isRecursive": "true"})
    assert len(names(rows)) == 1500 == len(set(names(rows)))
    server.add_group("cycle-a", ["cycle-b", "big00001"])
    server.add_group("cycle-b", ["cycle-a", "big00002", "big00001"])
    cycle = get(server, "/groups/members/user", {"samAccountName": "cycle-b", "isRecursive": "true"})
    assert names(cycle) == ["big00002", "big00001"]
    huge = start(local_adapi.large_group("huge", members=10001, distribution_members=1))
    assert len(names(get(huge, "/groups/members/user", {"samAccountName": "huge"}))) == 10001


def test_unknown_group_answer_is_switchable(adapi):
    """Requirements: REV-73; plan Directory protocol (deleted = 200 with zero rows)."""
    path = "/groups/members/user"
    assert get(adapi, path, {"samAccountName": "nobody", "isRecursive": "true"}).json() == []
    adapi.set_unknown_group_status(404)
    assert get(adapi, path, {"samAccountName": "nobody"}).status_code == 404
    assert get(adapi, path).status_code == 400


def test_latency_hook_delays_each_call(adapi):
    """Requirements: REV-73; plan A22, A25 (50 ms per call)."""
    adapi.set_latency("users", 0.2)
    began = time.monotonic()
    get(adapi, "/users", {"samAccountName": "local-admin"})
    assert time.monotonic() - began >= 0.2
    began = time.monotonic()
    get(adapi, "/groups")
    assert time.monotonic() - began < 0.2
    adapi.set_latency("users", 0)
    assert adapi.latency == {}


def test_pause_gate_blocks_until_released_and_answers_current_data(adapi):
    """Requirements: REV-73; plan A21, A26 (5) (pause mid-read)."""
    release = adapi.pause("members", name="keep-observers")
    result = {}

    def call():
        result["response"] = get(
            adapi, "/groups/members/user", {"samAccountName": "keep-observers"}
        )

    thread = threading.Thread(target=call)
    thread.start()
    assert adapi.wait_for_pause("members", name="keep-observers", timeout=5)
    assert names(get(adapi, "/groups/members/user", {"samAccountName": "keep-developers"}))
    assert thread.is_alive()
    adapi.add_member("keep-observers", "local-admin")
    release.set()
    thread.join(5)
    assert names(result["response"]) == ["local-viewer", "local-admin"]
    assert not adapi.wait_for_pause("users", timeout=0.05)


def test_pause_accepts_an_event_and_skip(adapi):
    """Requirements: REV-73; plan A21 (pause after part of a read)."""
    gate = threading.Event()
    assert adapi.pause("users", event=gate, skip=1) is gate
    assert get(adapi, "/users").status_code == 200
    thread = threading.Thread(target=lambda: get(adapi, "/users"))
    thread.start()
    assert adapi.wait_for_pause("users", timeout=5)
    adapi.release("users")
    thread.join(5)
    assert not thread.is_alive() and gate.is_set()


def test_request_log_and_counters(adapi):
    """Requirements: REV-73; plan A19, A22, A25, A26 (1) (count ADAPI calls)."""
    get(adapi, "/users", {"samAccountName": "local-admin"})
    get(adapi, "/users", {"samAccountName": "local-editor"})
    get(adapi, "/groups/members/user", {"samAccountName": "keep-developers", "isRecursive": "true"})
    get(adapi, "/groups", client_id="wrong")
    assert adapi.count("users") == 2 and adapi.count("members") == 1 and adapi.count() == 4
    log = adapi.log()
    assert log["counts"] == {"users": 2, "members": 1, "groups": 1}
    assert log["requests"][0] == {
        "method": "GET", "route": "users", "path": "/users",
        "query": {"samAccountName": ["local-admin"]}, "status": 200,
    }
    assert log["requests"][-1]["status"] == 401
    adapi.reset_log()
    assert adapi.count() == 0 and adapi.log() == {"requests": [], "counts": {}}


def test_data_mutations(adapi):
    """Requirements: REV-73; plan A4, A13, A20, A23 (disable, rename, duplicates)."""
    adapi.disable_user("local-editor")
    row = get(adapi, "/users", {"samAccountName": "local-editor"}).json()[0]
    assert row["userAccountControl"] == "514"
    adapi.enable_user("local-editor")
    adapi.rename("local-editor", "Renamed Editor")
    row = get(adapi, "/users", {"samAccountName": "local-editor"}).json()[0]
    assert (row["userAccountControl"], row["displayName"]) == ("512", "Renamed Editor")
    adapi.add_user("local-editor", displayName="Second Editor")
    assert names(get(adapi, "/users", {"samAccountName": "local-editor"})) == ["local-editor"] * 2
    adapi.remove_user("local-editor")
    assert names(get(adapi, "/users", {"samAccountName": "local-editor"})) == []
    adapi.add_group("new-team", ["local-admin"])
    adapi.add_member("new-team", "keep-observers")
    assert names(get(adapi, "/groups/members/user", {"samAccountName": "new-team"})) == [
        "local-admin", "local-viewer",
    ]
    admin = get(adapi, "/users", {"samAccountName": "local-admin"}).json()[0]
    assert "CN=new-team,OU=Groups,DC=keep,DC=local" in admin["memberOf"]
    adapi.remove_member("new-team", "local-admin")
    adapi.remove_group("new-team")
    assert names(get(adapi, "/groups", {"samAccountName": "new-team"})) == []
    with pytest.raises(KeyError):
        adapi.disable_user("nobody")


def test_set_hook_over_http(adapi):
    """Requirements: REV-73; plan Delivery and design (POST /__set hooks)."""
    def post(body):
        return HTTP.post(adapi.base_url + "/__set", json=body)

    assert post({
        "disable_user": "local-viewer",
        "rename": {"name": "keep-observers", "displayName": "Watchers", "kind": "group"},
        "add_group": {"name": "ops", "members": ["local-admin"], "distribution": True},
        "add_member": {"group": "ops", "member": "local-editor"},
        "search_cap": 2,
        "missing_value": None,
        "unknown_group_status": 404,
        "latency": {"groups": 0.01},
        "fail": [{"route": "members", "status": 503, "name": "keep-observers"}],
    }).status_code == 200
    viewer = get(adapi, "/users", {"samAccountName": "local-viewer"}).json()[0]
    assert viewer["userAccountControl"] == "514" and viewer["ipPhone"] is None
    assert len(get(adapi, "/users").json()) == 2
    assert get(adapi, "/groups/members/user", {"samAccountName": "nobody"}).status_code == 404
    path = "/groups/members/user"
    assert get(adapi, path, {"samAccountName": "keep-observers"}).status_code == 503
    assert names(get(adapi, path, {"samAccountName": "ops"})) == ["local-admin", "local-editor"]
    assert adapi.latency == {"groups": 0.01}
    log = HTTP.get(adapi.base_url + "/__log").json()
    assert log["counts"]["users"] == 2
    assert post({"reset_log": True, "clear_failures": True}).json()["counts"] == {}
    assert post({"disable_user": "nobody"}).status_code == 400
    assert post({"pause": {"route": "users"}}).status_code == 200
    assert post({"release": {"route": "users"}}).status_code == 200 and adapi.gates == []


def test_hooks_are_off_unless_loopback_or_enabled(start):
    """Requirements: REV-73; plan Delivery and design (fake-service pattern)."""
    server = start(enable_hooks=False)
    assert HTTP.post(server.base_url + "/__set", json={}).status_code == 404
    assert HTTP.get(server.base_url + "/__log").status_code == 404
    assert not local_adapi.LocalADAPI(host="0.0.0.0").enable_hooks
    assert local_adapi.LocalADAPI(host="127.0.0.1").enable_hooks


def test_a_refused_post_reads_its_body_before_answering(start):
    """A 404 sent while request bytes are unread lets Windows reset the connection.

    Requirements: REV-73; plan Delivery and design (fake-service pattern).
    """
    server = start(enable_hooks=False)
    big = b"{" + b" " * 1_000_000 + b"}"
    # Hooks off → 404; a product route without the ClientId header → 401.
    for path, status in (("/__set", 404), ("/no-such-route", 401)):
        response = HTTP.post(
            server.base_url + path, content=big, headers={"Content-Type": "application/json"}
        )
        assert response.status_code == status


def test_base_path_prefix(start):
    """Requirements: REV-73; plan Settings (KEEP_ADAPI_URL may carry a path)."""
    server = start(base_path="/adapi/api/")
    assert server.base_url.endswith("/adapi/api")
    assert names(get(server, "/users", {"samAccountName": "local-admin"})) == ["local-admin"]
    assert HTTP.get(f"http://127.0.0.1:{server.port}/users").status_code == 404


def test_scale_datasets():
    """Requirements: REV-73; plan A22, A25, A26 (7), A27 datasets."""
    data = local_adapi.build_directory(users=3000, groups=20, members_per_group=200)
    assert len(data["users"]) == 3000 and len(data["groups"]) == 20
    assert all(len(g["members"]) == 200 for g in data["groups"])
    directory = local_adapi.Directory(data)
    assert len(directory.recursive_members("team20")) == 200
    merged = local_adapi.merge_directories(local_adapi.sample_directory(), data)
    assert len(local_adapi.Directory(merged).users) == 3007
    roundtrip = local_adapi.Directory(directory.dump())
    assert roundtrip.dump() == directory.dump()


def test_log_lines_hold_no_client_id_or_values(start):
    """Requirements: REV-73; plan Decisions (ClientId secret), A10 spirit."""
    stream = io.StringIO()
    server = start(log_stream=stream, client_id="super-secret-client-id")
    get(server, "/users", {"samAccountName": "local-admin"}, client_id="super-secret-client-id")
    get(server, "/users", {"samAccountName": "local-admin"}, client_id="guessed-secret")
    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [line["status"] for line in lines] == [200, 401]
    assert lines[0]["params"] == ["samAccountName"] and lines[0]["path"] == "/users"
    text = stream.getvalue()
    assert "secret" not in text and "local-admin" not in text


def test_cli_arguments_and_environment(tmp_path):
    """Requirements: REV-73; plan Decisions (KEEP_ADAPI_CLIENT_ID), Design Local."""
    data = tmp_path / "directory.json"
    data.write_text(json.dumps(local_adapi.build_directory(users=3, groups=1)), encoding="utf-8")
    args = local_adapi.parse_args(
        ["--port", "0", "--data", str(data), "--client-id-env", "MY_ID", "--search-cap", "2"]
    )
    assert (args.host, args.port, args.client_id_env) == ("127.0.0.1", 0, "MY_ID")
    assert local_adapi.parse_args([]).port == 58092
    with pytest.raises(SystemExit):
        local_adapi.build_server(args, environ={})
    server = local_adapi.build_server(args, environ={"MY_ID": "abc"}, log_stream=io.StringIO())
    assert (server.client_id, server.search_cap, len(server.directory.users)) == ("abc", 2, 3)


def test_cli_process_serves_and_logs_without_the_client_id(tmp_path):
    """Requirements: REV-73; plan Design Local (started by Start on 58092)."""
    env = {"SYSTEMROOT": "C:\\Windows", "PATH": "", "KEEP_ADAPI_CLIENT_ID": "cli-secret-value",
           "PYTHONIOENCODING": "utf-8"}
    process = subprocess.Popen(
        [sys.executable, str(SCRIPTS / "local_adapi.py"), "--port", "0"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )
    try:
        listening = json.loads(process.stdout.readline())
        url = listening["listening"]
        assert listening["hooks"] is True
        response = HTTP.get(url + "/users", params={"samAccountName": "local-admin"},
                             headers={"ClientId": "cli-secret-value"}, timeout=10)
        assert names(response) == ["local-admin"]
        line = process.stdout.readline()
        assert json.loads(line)["status"] == 200
        assert "cli-secret-value" not in line and "local-admin" not in line
    finally:
        process.terminate()
        process.wait(10)
