"""Mock ADAPI directory for local sign-in and directory tests (REV-73, REV-76).

Copies the company AD REST API as the user described it (no real samples exist yet):

- ``GET /users`` and ``GET /groups`` search the directory. Query parameters ``aNR``,
  ``samAccountName``, ``distinguishedName``, ``commonName``, ``mailNickName``,
  ``objectGuid`` and ``customFilter`` (a raw RFC 4515 LDAP filter) are OR'ed. Each
  value is exact and case-insensitive unless it contains ``*`` wildcards. ``aNR``
  (ambiguous name resolution) is a prefix match on ``sAMAccountName``, ``displayName``,
  ``givenName``, ``sn``, ``cn`` and ``mail`` (guessed from AD). ``attributesToLoad``
  (repeated or comma-separated) limits the returned attributes; ``sAMAccountName`` is
  always returned. No filter parameter returns everything. Results are capped silently
  at 1,000 rows. Empty parameter values are ignored (the parameters are nullable) and
  unknown parameters are ignored, like ASP.NET binding; parameter names are
  case-insensitive.
- ``GET /users/user`` takes the same parameters as ``/users`` and answers the same rows;
  Keep calls it for one person by exact ``samAccountName`` and gets a list holding one
  object. No match answers 200 with ``[]`` (switchable with ``unknown_user_status``,
  because the real behavior is unknown).
- ``/users`` rows carry ``cn, description, displayName, distinguishedName, givenName,
  ipPhone, mail, mailNickname, memberOf, objectGUID, objectSid, sAMAccountName, sn,
  userAccountControl``; disabled accounts (``"514"``) are returned too, and Keep ignores
  ``userAccountControl`` (REV-76). ``/groups`` rows
  carry ``cn, description, givenName, distinguishedName, groupType, mail, mailNickName,
  managedBy, memberOf, objectGuid, sAMAccountName`` (note the casing). A group's
  ``displayName``, the computed ``member`` DNs and ``objectClass`` are returned only when
  ``attributesToLoad`` asks for them. Missing string values are ``""`` (switchable to
  ``null`` with ``missing_value``); ``memberOf`` is always a list.
- ``GET /groups/members/user?samAccountName=<group>&isRecursive=true`` returns every user
  reached through the group, nested and distribution groups included, cycles handled, no
  cap, rows with only ``sAMAccountName, distinguishedName, objectGUID,
  userAccountControl``. ``isRecursive=false`` returns direct user members only. An
  unknown group answers 200 with ``[]`` (switchable to 404 with ``unknown_group_status``
  because the real behavior is unknown). A missing ``samAccountName`` answers 400.
- Every route needs the ``ClientId`` header; a missing or wrong value answers 401. A
  malformed ``customFilter`` answers 400. Unknown paths answer 404.

Paths are matched after an optional ``--base-path`` prefix, so ``KEEP_ADAPI_URL`` may end
in a path. Test hooks (latency, injected failures, pause gates, data changes, the search
cap, the request log) are Python methods on :class:`LocalADAPI` and, over HTTP,
``POST /__set`` (JSON) and ``GET /__log``. The hooks answer only when the server is bound
to a loopback address or started with ``--enable-hooks``; they don't need the ClientId.

Each request prints one JSON line with the method, path, status, parameter *names* and
duration: no ClientId, no parameter values and no usernames.

    python scripts/local_adapi.py --host 127.0.0.1 --port 58092 \
        --data .local/identity/adapi-directory.json --client-id-env KEEP_ADAPI_CLIENT_ID
"""

import argparse
import hashlib
import hmac
import ipaddress
import json
import os
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

BASE_DN = "DC=keep,DC=local"
SEARCH_CAP = 1000
SECURITY_GROUP = "-2147483646"
DISTRIBUTION_GROUP = "8"
ACTIVE = "512"
DISABLED = "514"
COMPUTER = "4096"
SUPERADMIN_GROUP = "keep-administrators"
ROUTES = ("users", "user", "groups", "members")
USER_ATTRIBUTES = (
    "cn", "description", "displayName", "distinguishedName", "givenName", "ipPhone",
    "mail", "mailNickname", "memberOf", "objectGUID", "objectSid", "sAMAccountName", "sn",
    "userAccountControl",
)
GROUP_ATTRIBUTES = (
    "cn", "description", "givenName", "distinguishedName", "groupType", "mail",
    "mailNickName", "managedBy", "memberOf", "objectGuid", "sAMAccountName",
)
MEMBER_ATTRIBUTES = ("sAMAccountName", "distinguishedName", "objectGUID", "userAccountControl")
# Query parameter (casefolded) -> attributes it matches.
PARAMETERS = {
    "samaccountname": ("sAMAccountName",),
    "distinguishedname": ("distinguishedName",),
    "commonname": ("cn",),
    "mailnickname": ("mailNickname", "mailNickName"),
    "objectguid": ("objectGUID", "objectGuid"),
}
ANR_ATTRIBUTES = ("sAMAccountName", "displayName", "givenName", "sn", "cn", "mail")
_NAMESPACE = uuid.UUID("6f5b0c4e-2a51-4d3f-9a3c-6b7f3b0e7a10")


# --- RFC 4515 filters -------------------------------------------------------------------


class FilterError(ValueError):
    """A customFilter that isn't a valid (supported) RFC 4515 filter."""


def _decode_value(raw):
    out = bytearray()
    i = 0
    while i < len(raw):
        char = raw[i]
        if char == "\\":
            pair = raw[i + 1 : i + 3]
            if len(pair) != 2 or any(c not in "0123456789abcdefABCDEF" for c in pair):
                raise FilterError("bad escape in filter value")
            out.append(int(pair, 16))
            i += 3
            continue
        if char in "()\x00":
            raise FilterError("unescaped character in filter value")
        out.extend(char.encode("utf-8"))
        i += 1
    try:
        return out.decode("utf-8")
    except UnicodeDecodeError as error:
        raise FilterError("filter value is not UTF-8") from error


def _split_wildcards(raw):
    """Split a raw (escaped) value on unescaped ``*``; escapes stay intact."""
    parts, current, i = [], [], 0
    while i < len(raw):
        if raw[i] == "\\":
            current.append(raw[i : i + 3])
            i += 3
        elif raw[i] == "*":
            parts.append("".join(current))
            current = []
            i += 1
        else:
            current.append(raw[i])
            i += 1
    parts.append("".join(current))
    return parts


class _Parser:
    def __init__(self, text):
        self.text = text
        self.pos = 0

    def parse(self):
        node = self._filter()
        if self.pos != len(self.text):
            raise FilterError("trailing characters after filter")
        return node

    def _peek(self):
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def _expect(self, char):
        if self._peek() != char:
            raise FilterError(f"expected {char!r} at {self.pos}")
        self.pos += 1

    def _filter(self):
        self._expect("(")
        char = self._peek()
        if char in ("&", "|"):
            self.pos += 1
            children = []
            while self._peek() == "(":
                children.append(self._filter())
            if not children:
                raise FilterError("empty filter list")
            node = ("and" if char == "&" else "or", children)
        elif char == "!":
            self.pos += 1
            node = ("not", self._filter())
        else:
            node = self._item()
        self._expect(")")
        return node

    def _item(self):
        start = self.pos
        while self._peek() and (self._peek().isalnum() or self._peek() in "-;."):
            self.pos += 1
        attribute = self.text[start : self.pos]
        if not attribute:
            raise FilterError("missing attribute name")
        if self._peek() != "=":
            raise FilterError("only equality, presence and substring filters are supported")
        self.pos += 1
        start = self.pos
        while self._peek() and self._peek() != ")":
            if self._peek() == "(":
                raise FilterError("unescaped '(' in filter value")
            self.pos += 2 if self._peek() == "\\" else 1
        self.pos = min(self.pos, len(self.text))
        raw = self.text[start : self.pos]
        if raw == "*":
            return ("present", attribute)
        parts = _split_wildcards(raw)
        if len(parts) == 1:
            return ("eq", attribute, _decode_value(raw))
        middle = parts[1:-1]
        if any(part == "" for part in middle):
            raise FilterError("empty substring between wildcards")
        return (
            "sub",
            attribute,
            _decode_value(parts[0]),
            [_decode_value(part) for part in middle],
            _decode_value(parts[-1]),
        )


def parse_filter(text):
    """Parse an RFC 4515 filter; a bare ``attr=value`` gets its parentheses added."""
    text = (text or "").strip()
    if not text:
        raise FilterError("empty filter")
    if not text.startswith("("):
        text = f"({text})"
    return _Parser(text).parse()


def escape_filter_value(value):
    """RFC 4515 escaping of ``\\ * ( )`` and NUL (what Keep must send)."""
    return "".join("\\%02x" % ord(c) if c in "\\*()\x00" else c for c in value)


def _values(row, attribute):
    wanted = attribute.casefold()
    for key, value in row.items():
        if key.casefold() == wanted:
            if value is None:
                return []
            return [str(v) for v in value] if isinstance(value, list) else [str(value)]
    return []


def _substring(value, initial, middle, final):
    value = value.casefold()
    initial, final = initial.casefold(), final.casefold()
    if not value.startswith(initial):
        return False
    pos = len(initial)
    for part in middle:
        found = value.find(part.casefold(), pos)
        if found < 0:
            return False
        pos = found + len(part)
    return len(value) - pos >= len(final) and value.endswith(final)


def matches(node, row):
    """Whether a parsed filter matches a row (attribute names and values case-insensitive)."""
    kind = node[0]
    if kind == "and":
        return all(matches(child, row) for child in node[1])
    if kind == "or":
        return any(matches(child, row) for child in node[1])
    if kind == "not":
        return not matches(node[1], row)
    values = _values(row, node[1])
    if kind == "present":
        return bool(values)
    if kind == "eq":
        wanted = node[2].casefold()
        return any(value.casefold() == wanted for value in values)
    return any(_substring(value, node[2], node[3], node[4]) for value in values)


def _pattern(value, prefix=False):
    """A query parameter value as a matcher: exact unless it has ``*``."""
    if prefix and not value.endswith("*"):
        value += "*"
    if "*" not in value:
        return ("eq", value)
    parts = value.split("*")
    return ("sub", parts[0], [part for part in parts[1:-1] if part], parts[-1])


def _matches_pattern(pattern, values):
    if pattern[0] == "eq":
        wanted = pattern[1].casefold()
        return any(value.casefold() == wanted for value in values)
    return any(_substring(value, pattern[1], pattern[2], pattern[3]) for value in values)


# --- directory data ---------------------------------------------------------------------


def _key(name):
    return str(name).casefold()


def _dn_escape(value):
    return "".join("\\" + c if c in ',+"\\<>;=' else c for c in value)


def _guid(kind, name):
    return str(uuid.uuid5(_NAMESPACE, f"{kind}:{_key(name)}"))


def _sid(name):
    rid = int.from_bytes(hashlib.sha256(_key(name).encode()).digest()[:3], "big") + 1000
    return f"S-1-5-21-1004336348-1177238915-682003330-{rid}"


def _unique(values):
    return list(dict.fromkeys(values))


def _user(entry):
    entry = dict(entry)
    name = entry.pop("sAMAccountName", None) or entry.pop("name")
    disabled = entry.pop("disabled", None)
    display = entry.get("displayName") or name
    first, _, last = display.partition(" ")
    cn = entry.get("cn") or display
    uac = entry.get("userAccountControl")
    if uac is None:
        uac = COMPUTER if name.endswith("$") else (DISABLED if disabled else ACTIVE)
    row = {
        "cn": cn,
        "description": "",
        "displayName": display,
        "distinguishedName": f"CN={_dn_escape(cn)},OU=People,{BASE_DN}",
        "givenName": first,
        "ipPhone": "",
        "mail": f"{name}@keep.local",
        "mailNickname": name,
        "memberOf": [],
        "objectGUID": _guid("user", name),
        "objectSid": _sid(name),
        "sAMAccountName": name,
        "sn": last,
        "userAccountControl": str(uac),
    }
    row.update(entry)
    row["userAccountControl"] = str(row["userAccountControl"])
    row["memberOf"] = list(row.get("memberOf") or [])
    return row


def _group(entry):
    entry = dict(entry)
    name = entry.pop("sAMAccountName", None) or entry.pop("name")
    members = list(entry.pop("members", []) or [])
    distribution = entry.pop("distribution", False)
    cn = entry.get("cn") or name
    row = {
        "cn": cn,
        "description": "",
        "givenName": "",
        "distinguishedName": f"CN={_dn_escape(cn)},OU=Groups,{BASE_DN}",
        "groupType": DISTRIBUTION_GROUP if distribution else SECURITY_GROUP,
        "mail": "",
        "mailNickName": name,
        "managedBy": "",
        "memberOf": [],
        "objectGuid": _guid("group", name),
        "sAMAccountName": name,
    }
    row.update(entry)
    row["memberOf"] = list(row.get("memberOf") or [])
    return row, members


class Directory:
    """The mock's data. ``users`` and ``groups`` are lists, so duplicate names are allowed
    (a lookup that must be refused as ambiguous). Group ``members`` name users or groups;
    ``memberOf`` and ``member`` are computed from them.
    """

    def __init__(self, data=None):
        self.lock = threading.RLock()
        self.load(data if data is not None else sample_directory())

    def load(self, data):
        with self.lock:
            self.users = [_user(entry) for entry in data.get("users", [])]
            self.groups, self.members = [], []
            for entry in data.get("groups", []):
                row, members = _group(entry)
                self.groups.append(row)
                self.members.append(members)
            self._index()

    def dump(self):
        """The data in the JSON-file form ``load`` accepts."""
        with self.lock:
            users = [dict(row) for row in self.users]
            groups = [dict(row, members=list(m)) for row, m in zip(self.groups, self.members)]
            return {"users": users, "groups": groups}

    def _index(self):
        self.user_index, self.group_index = {}, {}
        for row in self.users:
            self.user_index.setdefault(_key(row["sAMAccountName"]), []).append(row)
        for position, row in enumerate(self.groups):
            self.group_index.setdefault(_key(row["sAMAccountName"]), []).append(position)
        member_of, member_dns = {}, []
        for row, members in zip(self.groups, self.members):
            dns = []
            for name in members:
                member_of.setdefault(_key(name), []).append(row["distinguishedName"])
                dns.extend(r["distinguishedName"] for r in self.user_index.get(_key(name), []))
                dns.extend(
                    self.groups[p]["distinguishedName"]
                    for p in self.group_index.get(_key(name), [])
                )
            member_dns.append(dns)
        self.user_rows = []
        for row in self.users:
            classes = ["top", "person", "organizationalPerson", "user"]
            if row["sAMAccountName"].endswith("$"):
                classes.append("computer")
            extra = member_of.get(_key(row["sAMAccountName"]), [])
            self.user_rows.append(
                dict(row, memberOf=_unique(row["memberOf"] + extra), objectClass=classes)
            )
        self.group_rows = []
        for row, dns in zip(self.groups, member_dns):
            extra = member_of.get(_key(row["sAMAccountName"]), [])
            self.group_rows.append(
                dict(row, memberOf=_unique(row["memberOf"] + extra), member=dns,
                     objectClass=["top", "group"])
            )
        # Positions by sAMAccountName for the exact-name fast path in search().
        self.sam_positions = {"users": {}, "groups": {}}
        for route, rows in (("users", self.user_rows), ("groups", self.group_rows)):
            for position, row in enumerate(rows):
                self.sam_positions[route].setdefault(_key(row["sAMAccountName"]), []).append(
                    position
                )

    @staticmethod
    def _exact_names(query):
        """The names when every test is an exact sAMAccountName match, else None.

        ``samAccountName`` values without ``*`` and ``customFilter`` values that are one
        ``(sAMAccountName=x)`` or an OR of them qualify; anything else uses the scan.
        """
        if any(query.get(name) for name in PARAMETERS if name != "samaccountname"):
            return None
        if query.get("anr"):
            return None
        names = []
        for value in query.get("samaccountname", []):
            if "*" in value:
                return None
            names.append(value)
        for value in query.get("customfilter", []):
            node = parse_filter(value)
            for child in node[1] if node[0] == "or" else [node]:
                if child[0] != "eq" or child[1].casefold() != "samaccountname":
                    return None
                names.append(child[2])
        return names or None

    def search(self, route, query, cap):
        """Rows for ``/users`` or ``/groups``; raises FilterError on a bad customFilter."""
        tests = []
        for value in query.get("customfilter", []):
            node = parse_filter(value)
            tests.append(lambda row, node=node: matches(node, row))
        for name, attributes in PARAMETERS.items():
            for value in query.get(name, []):
                tests.append(
                    lambda row, p=_pattern(value), a=attributes: any(
                        _matches_pattern(p, _values(row, attr)) for attr in a
                    )
                )
        for value in query.get("anr", []):
            tests.append(
                lambda row, p=_pattern(value, prefix=True): any(
                    _matches_pattern(p, _values(row, attr)) for attr in ANR_ATTRIBUTES
                )
            )
        names = self._exact_names(query)
        with self.lock:
            rows = self.user_rows if route == "users" else self.group_rows
            if names is not None:
                # Same rows, order and cap as the scan below, from the name index, so
                # scale tests with 10,000 users don't spend a second per batch lookup.
                index = self.sam_positions["users" if route == "users" else "groups"]
                positions = sorted({p for name in names for p in index.get(_key(name), ())})
                return [dict(rows[p]) for p in positions[:cap]]
            found = []
            for row in rows:
                if len(found) >= cap:
                    break
                if not tests or any(test(row) for test in tests):
                    found.append(dict(row))
            return found

    def recursive_members(self, group, recursive=True):
        """Users reached through ``group`` (breadth first, each once); None if no such group."""
        with self.lock:
            start = self.group_index.get(_key(group))
            if not start:
                return None
            seen_groups, seen_users, out = set(start), set(), []
            queue = list(start)
            while queue:
                position = queue.pop(0)
                for name in self.members[position]:
                    for row in self.user_index.get(_key(name), []):
                        if id(row) not in seen_users:
                            seen_users.add(id(row))
                            out.append({attr: row[attr] for attr in MEMBER_ATTRIBUTES})
                    if not recursive:
                        continue
                    for child in self.group_index.get(_key(name), []):
                        if child not in seen_groups:
                            seen_groups.add(child)
                            queue.append(child)
            return out

    # Mutations; each raises KeyError for an unknown name and re-indexes.
    def _users_named(self, name):
        rows = [row for row in self.users if _key(row["sAMAccountName"]) == _key(name)]
        if not rows:
            raise KeyError(f"no user {name!r}")
        return rows

    def _groups_named(self, name):
        positions = [
            p for p, row in enumerate(self.groups) if _key(row["sAMAccountName"]) == _key(name)
        ]
        if not positions:
            raise KeyError(f"no group {name!r}")
        return positions

    def add_user(self, name, **attributes):
        """Append a user; an existing name makes a duplicate (an ambiguous lookup)."""
        with self.lock:
            self.users.append(_user({"sAMAccountName": name, **attributes}))
            self._index()

    def remove_user(self, name):
        with self.lock:
            self._users_named(name)
            self.users = [r for r in self.users if _key(r["sAMAccountName"]) != _key(name)]
            self._index()

    def add_group(self, name, members=(), distribution=False, **attributes):
        with self.lock:
            row, members = _group(
                {"sAMAccountName": name, "members": list(members),
                 "distribution": distribution, **attributes}
            )
            self.groups.append(row)
            self.members.append(members)
            self._index()

    def remove_group(self, name):
        with self.lock:
            drop = set(self._groups_named(name))
            self.groups = [row for p, row in enumerate(self.groups) if p not in drop]
            self.members = [m for p, m in enumerate(self.members) if p not in drop]
            self._index()

    def add_member(self, group, member):
        with self.lock:
            for position in self._groups_named(group):
                if _key(member) not in {_key(m) for m in self.members[position]}:
                    self.members[position].append(member)
            self._index()

    def remove_member(self, group, member):
        with self.lock:
            for position in self._groups_named(group):
                self.members[position] = [
                    m for m in self.members[position] if _key(m) != _key(member)
                ]
            self._index()

    def rename(self, name, display_name, kind=None):
        """Set ``displayName`` on users and/or groups named ``name``."""
        with self.lock:
            rows = []
            if kind in (None, "user"):
                rows += [r for r in self.users if _key(r["sAMAccountName"]) == _key(name)]
            if kind in (None, "group", "team"):
                rows += [r for r in self.groups if _key(r["sAMAccountName"]) == _key(name)]
            if not rows:
                raise KeyError(f"no user or group {name!r}")
            for row in rows:
                row["displayName"] = display_name
            self._index()


# --- datasets ---------------------------------------------------------------------------


def sample_directory():
    """The default local data: the three local users and their groups (local-admin in the
    super-admin group keep-administrators), a user whose password mixes Hebrew and
    English, a computer account and a distribution-group chain
    (keep-developers -> keep-dev-announce (distribution) -> local-distribution).
    """
    return {
        "users": [
            {"sAMAccountName": "local-admin", "displayName": "Local Administrator"},
            {"sAMAccountName": "local-editor", "displayName": "Local Editor"},
            {"sAMAccountName": "local-viewer", "displayName": "Local Viewer"},
            {"sAMAccountName": "local-hebrew", "displayName": "משתמש Local Hebrew"},
            {"sAMAccountName": "local-distribution", "displayName": "Local Distribution"},
            {"sAMAccountName": "WS-0001$", "displayName": "WS-0001"},
        ],
        "groups": [
            {"sAMAccountName": SUPERADMIN_GROUP, "cn": "Keep Administrators",
             "displayName": "Keep Administrators", "members": ["local-admin"]},
            {"sAMAccountName": "keep-developers", "cn": "Developers", "displayName": "Developers",
             "members": ["local-editor", "local-hebrew", "WS-0001$", "keep-dev-announce"]},
            {"sAMAccountName": "keep-dev-announce", "cn": "Developer Announcements",
             "distribution": True, "members": ["local-distribution"]},
            {"sAMAccountName": "keep-observers", "cn": "Observers", "displayName": "Observers",
             "members": ["local-viewer"]},
        ],
    }


def build_directory(users=3000, groups=20, members_per_group=200, user_prefix="user",
                    group_prefix="team"):
    """Synthetic scale data: ``users`` active users (``user00001``…) and ``groups`` security
    groups (``team01``…) whose members are consecutive, wrapping slices of the users.
    """
    width = max(5, len(str(users)))
    names = [f"{user_prefix}{i:0{width}d}" for i in range(1, users + 1)]
    data = {
        "users": [{"sAMAccountName": n, "displayName": f"User {n[len(user_prefix):]}"}
                  for n in names],
        "groups": [],
    }
    for g in range(groups):
        start = (g * members_per_group) % max(users, 1)
        members = [names[(start + i) % users] for i in range(min(members_per_group, users))]
        data["groups"].append({"sAMAccountName": f"{group_prefix}{g + 1:02d}", "members": members})
    return data


def large_group(name="big-team", members=1500, distribution_members=100, user_prefix="big"):
    """One security group reaching ``members`` users: ``members - distribution_members``
    direct members plus a distribution group ``<name>-dl`` holding the rest and pointing
    back at the parent (a cycle). ``large_group(members=10001)`` exceeds Keep's
    10,000-object cap.
    """
    width = max(5, len(str(members)))
    names = [f"{user_prefix}{i:0{width}d}" for i in range(1, members + 1)]
    split = members - distribution_members
    groups = [{"sAMAccountName": name,
               "members": names[:split] + ([f"{name}-dl"] if distribution_members else [])}]
    if distribution_members:
        groups.append({"sAMAccountName": f"{name}-dl", "distribution": True,
                       "members": names[split:] + [name]})
    return {
        "users": [{"sAMAccountName": n, "displayName": f"Member {n[len(user_prefix):]}"}
                  for n in names],
        "groups": groups,
    }


def merge_directories(*directories):
    return {
        "users": [u for d in directories for u in d.get("users", [])],
        "groups": [g for d in directories for g in d.get("groups", [])],
    }


# --- server -----------------------------------------------------------------------------


class _Gate:
    def __init__(self, route, name, skip, event):
        self.route, self.name, self.skip = route, name, skip
        self.release = event if event is not None else threading.Event()
        self.arrived = 0


def _list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _is_loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class LocalADAPI:
    """In-process mock ADAPI. ``start()`` binds (port 0 picks a free port) and returns
    self; ``stop()`` releases every pause gate and closes the server. A context manager.
    Routes for hooks and counters: ``users``, ``user`` (``/users/user``), ``groups``,
    ``members`` (``*`` = any).
    """

    def __init__(self, directory=None, client_id="local-adapi-client-id", host="127.0.0.1",
                 port=0, base_path="", search_cap=SEARCH_CAP, enable_hooks=None,
                 log_stream=None, gate_timeout=120.0):
        self.directory = directory if isinstance(directory, Directory) else Directory(directory)
        self.client_id = client_id
        self.host, self.port = host, port
        self.base_path = "/" + base_path.strip("/") if base_path.strip("/") else ""
        self.search_cap = search_cap
        self.enable_hooks = _is_loopback(host) if enable_hooks is None else enable_hooks
        self.log_stream = log_stream
        self.gate_timeout = gate_timeout
        self.unknown_group_status = 200
        self.unknown_user_status = 200
        self.missing_value = ""
        self._lock = threading.Lock()
        self._arrival = threading.Condition(self._lock)
        self.latency = {}
        self.failures = []
        self.gates = []
        self.requests = []
        self.counts = {}
        self._server = None
        self._thread = None

    # Lifecycle.
    def start(self):
        self._server = _Server((self.host, self.port), _Handler)
        self._server.adapi = self
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self._thread.start()
        return self

    def stop(self):
        self.release_all()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._thread.join(timeout=5)
            self._server = None

    def serve_forever(self):
        self._thread.join()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    @property
    def base_url(self):
        return f"http://{self.host}:{self.port}{self.base_path}"

    # Hooks.
    def set_latency(self, route, seconds):
        """Delay every call on ``route`` by ``seconds`` (0 clears it)."""
        with self._lock:
            if seconds:
                self.latency[route] = float(seconds)
            else:
                self.latency.pop(route, None)

    def fail(self, route, status=500, name=None, times=None, body=None):
        """Answer ``status`` on ``route``. With ``name``, only for requests that name it
        (``samAccountName``, the members group) or whose result has a row with that
        sAMAccountName (so a ``customFilter`` batch containing it fails too). ``times``
        limits how many requests fail; None = until ``clear_failures()``.
        """
        with self._lock:
            self.failures.append({
                "route": route, "status": int(status), "name": name, "times": times,
                "body": body if body is not None else {"error": "injected failure"},
            })

    def clear_failures(self):
        with self._lock:
            self.failures.clear()

    def pause(self, route, name=None, event=None, skip=0):
        """Block requests on ``route`` (only those naming ``name``, matched like ``fail``)
        until the returned (or given) ``threading.Event`` is set or ``release`` is called.
        ``skip`` lets that many matching requests through first. A blocked search answers
        with the data as it is when released.
        """
        gate = _Gate(route, name, skip, event)
        with self._lock:
            self.gates.append(gate)
        return gate.release

    def wait_for_pause(self, route, name=None, count=1, timeout=10.0):
        """Wait until ``count`` requests have reached the gate; True on success."""
        deadline = time.monotonic() + timeout
        with self._arrival:
            while True:
                gate = next((g for g in self.gates if g.route == route
                             and (g.name or None) == (name or None)), None)
                if gate is not None and gate.arrived >= count:
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._arrival.wait(remaining)

    def release(self, route=None, name=None):
        """Open and remove the gates for ``route``/``name`` (None = any)."""
        with self._lock:
            keep = []
            for gate in self.gates:
                if (route is None or gate.route == route) and (name is None or gate.name == name):
                    gate.release.set()
                else:
                    keep.append(gate)
            self.gates = keep

    def release_all(self):
        self.release()

    def set_search_cap(self, cap):
        self.search_cap = int(cap)

    def set_unknown_group_status(self, status):
        self.unknown_group_status = int(status)

    def set_unknown_user_status(self, status):
        self.unknown_user_status = int(status)

    def add_user(self, name, **attributes):
        self.directory.add_user(name, **attributes)

    def remove_user(self, name):
        self.directory.remove_user(name)

    def add_group(self, name, members=(), distribution=False, **attributes):
        self.directory.add_group(name, members, distribution, **attributes)

    def remove_group(self, name):
        self.directory.remove_group(name)

    def add_member(self, group, member):
        self.directory.add_member(group, member)

    def remove_member(self, group, member):
        self.directory.remove_member(group, member)

    def rename(self, name, display_name, kind=None):
        self.directory.rename(name, display_name, kind)

    def load(self, data):
        self.directory.load(data)

    def reset_log(self):
        with self._lock:
            self.requests.clear()
            self.counts.clear()

    def log(self):
        """``{"requests": [{method, route, path, query, status}], "counts": {route: n}}``."""
        with self._lock:
            return {"requests": [dict(r) for r in self.requests], "counts": dict(self.counts)}

    def count(self, route=None):
        """Requests on ``route`` (all routes when None) since the last reset."""
        with self._lock:
            return sum(self.counts.values()) if route is None else self.counts.get(route, 0)

    def apply(self, body):
        """Apply a ``POST /__set`` JSON body (see the unit tests for each key)."""
        for route, seconds in (body.get("latency") or {}).items():
            self.set_latency(route, seconds)
        if body.get("clear_failures"):
            self.clear_failures()
        for rule in _list(body.get("fail")):
            self.fail(rule["route"], rule.get("status", 500), rule.get("name"),
                      rule.get("times"), rule.get("body"))
        if "release" in body:
            rule = body["release"] or {}
            self.release(rule.get("route"), rule.get("name"))
        if body.get("release_all"):
            self.release_all()
        for rule in _list(body.get("pause")):
            self.pause(rule["route"], rule.get("name"), skip=int(rule.get("skip", 0)))
        if "data" in body:
            self.load(body["data"])
        for user in _list(body.get("add_user")):
            user = dict(user)
            self.add_user(user.pop("sAMAccountName", None) or user.pop("name"), **user)
        for name in _list(body.get("remove_user")):
            self.remove_user(name)
        for group in _list(body.get("add_group")):
            group = dict(group)
            name = group.pop("sAMAccountName", None) or group.pop("name")
            self.add_group(name, group.pop("members", []), group.pop("distribution", False),
                           **group)
        for name in _list(body.get("remove_group")):
            self.remove_group(name)
        for rule in _list(body.get("add_member")):
            self.add_member(rule["group"], rule["member"])
        for rule in _list(body.get("remove_member")):
            self.remove_member(rule["group"], rule["member"])
        for rule in _list(body.get("rename")):
            self.rename(rule["name"], rule["displayName"], rule.get("kind"))
        if "search_cap" in body:
            self.set_search_cap(body["search_cap"])
        if "unknown_group_status" in body:
            self.set_unknown_group_status(body["unknown_group_status"])
        if "unknown_user_status" in body:
            self.set_unknown_user_status(body["unknown_user_status"])
        if "missing_value" in body:
            self.missing_value = body["missing_value"]
        if body.get("reset_log"):
            self.reset_log()

    # Request handling (handler threads).
    def _record(self, method, route, path, query, status):
        with self._lock:
            self.requests.append({"method": method, "route": route, "path": path,
                                  "query": {k: list(v) for k, v in query.items()},
                                  "status": status})
            self.counts[route] = self.counts.get(route, 0) + 1

    def _wait_gates(self, route, names):
        blocked = []
        with self._lock:
            for gate in self.gates:
                if gate.route not in (route, "*") or (gate.name and _key(gate.name) not in names):
                    continue
                if gate.skip > 0:
                    gate.skip -= 1
                    continue
                gate.arrived += 1
                blocked.append(gate)
            if blocked:
                self._arrival.notify_all()
        for gate in blocked:
            gate.release.wait(self.gate_timeout)
        return bool(blocked)

    def _failure(self, route, names):
        with self._lock:
            for rule in self.failures:
                if rule["route"] not in (route, "*"):
                    continue
                if rule["name"] and _key(rule["name"]) not in names:
                    continue
                if rule["times"] is not None:
                    if rule["times"] <= 0:
                        continue
                    rule["times"] -= 1
                return rule
        return None

    def _delay(self, route):
        with self._lock:
            delay = self.latency.get(route, 0.0) + self.latency.get("*", 0.0)
        if delay:
            time.sleep(delay)

    def handle(self, method, raw_path, headers):
        """``(status, body, route)`` for a request; status None means a hook request."""
        parts = urlsplit(raw_path)
        path = parts.path
        if self.base_path:
            if path != self.base_path and not path.startswith(self.base_path + "/"):
                return 404, {"error": "not found"}, "other"
            path = path[len(self.base_path):]
        path = path.rstrip("/") or "/"
        raw_query = parse_qs(parts.query, keep_blank_values=True)
        if path in ("/__set", "/__log"):
            if not self.enable_hooks:
                return 404, {"error": "not found"}, "hooks"
            return None, None, "hooks"
        route = {"/users": "users", "/users/user": "user", "/groups": "groups",
                 "/groups/members/user": "members"}.get(path, "other")
        status, body = self._serve(method, route, raw_query, headers)
        self._record(method, route, path, raw_query, status)
        return status, body, route

    def _serve(self, method, route, raw_query, headers):
        supplied = headers.get("ClientId")
        expected = str(self.client_id).encode()
        if supplied is None or not hmac.compare_digest(supplied.encode(), expected):
            return 401, {"error": "unauthorized"}
        if route == "other":
            return 404, {"error": "not found"}
        if method != "GET":
            return 405, {"error": "method not allowed"}
        query = {}
        for key, values in raw_query.items():
            name = key.casefold().removesuffix("[]")
            query.setdefault(name, []).extend(v for v in values if v != "")
        if route == "members":
            return self._members(query)
        # /users/user searches like /users (REV-76); it keeps its own route for hooks.
        table = "users" if route == "user" else route
        try:
            rows = self.directory.search(table, query, self.search_cap)
        except FilterError as error:
            return 400, {"error": "invalid customFilter", "detail": str(error)}
        names = {_key(v) for v in query.get("samaccountname", [])}
        names |= {_key(row["sAMAccountName"]) for row in rows}
        self._delay(route)
        if self._wait_gates(route, names):
            rows = self.directory.search(table, query, self.search_cap)
        rule = self._failure(route, names)
        if rule:
            return rule["status"], rule["body"]
        if route == "user" and not rows and self.unknown_user_status != 200:
            return self.unknown_user_status, {"error": "user not found"}
        attributes = [a.strip() for v in query.get("attributestoload", [])
                      for a in v.split(",") if a.strip()]
        return 200, [self._project(table, row, attributes) for row in rows]

    def _project(self, route, row, attributes):
        if attributes:
            wanted = {a.casefold() for a in attributes} | {"samaccountname"}
            return {k: v for k, v in row.items() if k.casefold() in wanted}
        out = {}
        for attr in USER_ATTRIBUTES if route == "users" else GROUP_ATTRIBUTES:
            value = row.get(attr)
            if attr == "memberOf":
                out[attr] = list(value or [])
            else:
                out[attr] = value if value not in (None, "") else self.missing_value
        return out

    def _members(self, query):
        groups = query.get("samaccountname", [])
        if not groups:
            return 400, {"error": "samAccountName is required"}
        group = groups[-1]
        recursive = query.get("isrecursive", ["true"])[-1].casefold() != "false"
        names = {_key(group)}
        self._delay("members")
        self._wait_gates("members", names)
        rule = self._failure("members", names)
        if rule:
            return rule["status"], rule["body"]
        rows = self.directory.recursive_members(group, recursive)
        if rows is None:
            if self.unknown_group_status == 200:
                return 200, []
            return self.unknown_group_status, {"error": "group not found"}
        return 200, rows


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    adapi = None


class _Handler(BaseHTTPRequestHandler):
    server_version = "keep-local-adapi"

    def log_message(self, *args):
        return

    def _send(self, status, body):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _log(self, status, started):
        stream = self.server.adapi.log_stream
        if stream is None:
            return
        parts = urlsplit(self.path)
        line = {
            "method": self.command,
            "path": parts.path,
            "status": status,
            "params": sorted(parse_qs(parts.query, keep_blank_values=True)),
            "ms": round((time.monotonic() - started) * 1000, 1),
        }
        print(json.dumps(line), file=stream, flush=True)

    def _dispatch(self):
        started = time.monotonic()
        adapi = self.server.adapi
        try:
            # Read the whole body before answering: a reply sent while request bytes are
            # still unread lets Windows reset the connection (WinError 10053).
            length = int(self.headers.get("Content-Length") or 0)
            payload = self.rfile.read(length) if length > 0 else b""
            status, body, route = adapi.handle(self.command, self.path, self.headers)
            if route == "hooks" and status is None:
                status, body = self._hooks(adapi, payload)
        except Exception as error:  # a mock bug must still answer, never hang the client
            status, body = 500, {"error": "mock failure", "detail": type(error).__name__}
        try:
            self._send(status, body)
        finally:
            self._log(status, started)

    def _hooks(self, adapi, payload):
        if urlsplit(self.path).path.rstrip("/").endswith("/__log"):
            if self.command != "GET":
                return 405, {"error": "method not allowed"}
            return 200, adapi.log()
        if self.command != "POST":
            return 405, {"error": "method not allowed"}
        try:
            body = json.loads(payload or b"{}")
            if not isinstance(body, dict):
                raise ValueError("body must be an object")
            adapi.apply(body)
        except (ValueError, KeyError, TypeError) as error:
            return 400, {"error": "bad hook request", "detail": str(error)}
        return 200, {"ok": True, "counts": adapi.log()["counts"]}

    do_GET = _dispatch
    do_POST = _dispatch
    do_PUT = _dispatch
    do_DELETE = _dispatch


def load_data(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=58092)
    parser.add_argument("--data", help="directory JSON file; the built-in sample when omitted")
    parser.add_argument("--client-id-env", default="KEEP_ADAPI_CLIENT_ID",
                        help="environment variable holding the expected ClientId header value")
    parser.add_argument("--base-path", default="", help="path prefix before /users and /groups")
    parser.add_argument("--search-cap", type=int, default=SEARCH_CAP)
    parser.add_argument("--enable-hooks", action="store_true",
                        help="serve /__set and /__log on a non-loopback address too")
    return parser.parse_args(argv)


def build_server(args, environ=None, log_stream=None):
    """A configured (not started) LocalADAPI from parsed CLI arguments."""
    environ = os.environ if environ is None else environ
    client_id = environ.get(args.client_id_env)
    if not client_id:
        raise SystemExit(f"set {args.client_id_env} to the expected ClientId value")
    return LocalADAPI(
        load_data(args.data) if args.data else sample_directory(),
        client_id=client_id,
        host=args.host,
        port=args.port,
        base_path=args.base_path,
        search_cap=args.search_cap,
        enable_hooks=True if args.enable_hooks else None,
        log_stream=sys.stdout if log_stream is None else log_stream,
    )


def main(argv=None):
    adapi = build_server(parse_args(argv)).start()
    print(json.dumps({"listening": adapi.base_url, "hooks": adapi.enable_hooks}), flush=True)
    try:
        adapi.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        adapi.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
