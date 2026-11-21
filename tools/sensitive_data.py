"""Conservative workload-input classification and public telemetry redaction.

This is a second boundary after an explicit input manifest, not a claim to detect
all secrets. Public certificates and ordinary source/configuration remain usable.
"""
import json
from pathlib import PurePosixPath
import re
from urllib.parse import parse_qsl, urlencode, unquote, urlsplit, urlunsplit

REDACTED = "[redacted]"
_PRIVATE_DIRS = {'.git', '.ssh', '.aws', '.azure', '.gnupg', '.codex', '.claude', '.agents',
                 'secrets', 'credentials', 'private-keys'}
_PRIVATE_NAMES = {'.netrc', '.npmrc', '.pypirc', '.mcp.json', '.claude.json',
                  'credentials.json', 'credential.json', 'credentials.yaml', 'credentials.yml',
                  'service-account.json', 'service_account.json', 'token.json', 'tokens.json',
                  'access_tokens.json', 'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519',
                  'key.pem', 'private.pem', 'private_key.pem', 'private-key.pem'}
_SECRET_KEYS = {'password', 'passwd', 'pwd', 'secret', 'token', 'apikey', 'accesstoken',
                'refreshtoken', 'idtoken', 'clientsecret', 'privatekey', 'authorization',
                'proxyauthorization', 'awssecretaccesskey', 'awsaccesskeyid', 'sessiontoken',
                'connectionstring', 'databaseurl', 'apitoken', 'secretkey', 'passphrase', 'xapikey', 'xgoogapikey', 'xauthtoken', 'awssessiontoken', 'cookie', 'setcookie', 'sessionid', 'sessionkey', 'authtoken', 'credential', 'credentials', '비밀번호', '비밀키', '인증키', '암호'}
_PEM = re.compile(r'-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----[\s\S]*?(?:-----END(?: [A-Z0-9]+)* PRIVATE KEY-----|\Z)')
_TOKEN = re.compile(r'\b(?:sk-(?:ant-)?[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]{12,}|AKIA[A-Z0-9]{16}|ASIA[A-Z0-9]{16}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})\b')
_KEY_PATTERN = r'(?:x[-_](?:api[-_]key|goog[-_]api[-_]key|auth[-_]token)|aws[_ -]?session[_ -]?token|secret[_ -]?key|passphrase|cookie|set[_ -]?cookie|session[_ -]?id|session[_ -]?key|access[_ -]?token|refresh[_ -]?token|id[_ -]?token|api[_ -]?(?:key|token)|client[_ -]?secret|private[_ -]?key|aws[_ -]?secret[_ -]?access[_ -]?key|aws[_ -]?access[_ -]?key[_ -]?id|authorization|proxy[_ -]?authorization|password|passwd|pwd|secret|token|connection[_ -]?string|database[_ -]?url|비밀번호|비밀키|인증키|암호)'
# The value alternatives consume escaped quotes and multiline quoted values.
_QUOTED = re.compile(r'(?i)((?<![\w-])(?:["\']?'+_KEY_PATTERN+r'["\']?)\s*[:=]\s*)("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')')
_BARE = re.compile(r'(?i)((?<![\w-])(?:["\']?'+_KEY_PATTERN+r'["\']?)\s*[:=]\s*)(?![\"\'\[])([^\s,;&}\]\n\"\']+)')
_JSON_PAIR = re.compile(r'("(?:\\.|[^"\\])*"\s*:\s*)("(?:\\.|[^"\\])*")')
_URL = re.compile(r'https?://[^\s<>"\']+', re.IGNORECASE)


def _key(value):
    return re.sub(r'[\W_]+', '', str(value), flags=re.UNICODE).casefold()


def sensitive_key(value):
    return _key(value) in _SECRET_KEYS


def is_sensitive_path(relative):
    """Classify credential locations without treating every JSON/PEM as secret."""
    if not isinstance(relative, str):
        return True
    parts = PurePosixPath(relative.replace('\\', '/')).parts
    names = [p.casefold() for p in parts]
    if any(p in _PRIVATE_DIRS for p in names):
        return True
    if any(p.startswith('.env') and p not in {'.env.example', '.env.sample', '.env.template'} for p in names):
        return True
    name = names[-1] if names else ''
    return (name in _PRIVATE_NAMES or name.endswith(('.p12', '.pfx', '.jks', '.kdbx', '.key'))
            or bool(re.fullmatch(r'(?:service[-_]account|credentials?)[-_.].*\.(?:json|ya?ml)', name)))


def _placeholder(value):
    if value is None or value == '' or value == REDACTED:
        return True
    if not isinstance(value, str):
        return False
    text = value.strip()
    return (text.upper() in {'CHANGEME', 'CHANGE_ME', 'TODO', 'REPLACE_ME', 'YOUR_API_KEY', 'YOUR_TOKEN', 'EXAMPLE'}
            or bool(re.fullmatch(r'\$\{[^}]+\}|<[^<>]+>', text)))


def contains_sensitive_data(value):
    """Detect private keys, provider key formats and populated JSON secret fields."""
    if isinstance(value, bytes):
        encoding = 'utf-32' if value.startswith((b'\xff\xfe\0\0', b'\0\0\xfe\xff')) else 'utf-16' if value.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
        text = value.decode(encoding, errors='replace')
    elif isinstance(value, str):
        text = value
    else:
        raise TypeError('Secret inspection expects bytes or text')
    if _PEM.search(text) or _TOKEN.search(text):
        return True
    # Partial/embedded JSON must not bypass the content gate merely because the
    # surrounding document is incomplete. Preserve harmless placeholder config.
    normalized = text
    for _ in range(3):
        normalized = normalized.replace('\\\"', '\"')
    for match in _JSON_PAIR.finditer(normalized):
        try:
            key = json.loads(match.group(1).rsplit(':', 1)[0].strip())
            content = json.loads(match.group(2))
        except ValueError:
            continue
        if sensitive_key(key) and not _placeholder(content):
            return True
    for match in _URL.finditer(text):
        if _redact_url(match) != match.group(0):
            return True
    # Literal credential assignments are configuration, not ordinary identifiers.
    literal = re.compile(r"(?im)^\s*(?:export\s+)?[\"']?(" + _KEY_PATTERN + r")[\"']?\s*[:=]\s*([\"'])([^\r\n]*?)\2")
    for match in literal.finditer(text):
        if not _placeholder(match.group(3)):
            return True
    yaml_value = re.compile(r"(?im)^\s*(" + _KEY_PATTERN + r")\s*:\s*([^\r\n#]+)")
    for match in yaml_value.finditer(text):
        content = match.group(2).strip()
        # Python annotations and mapping/object structure are not literal values.
        if not content or content[:1] in {'{', '[', '|', '>'} or re.match(r"(?:str|bytes|int|bool|Optional|Union)(?:\s|\[|$)", content):
            continue
        if not _placeholder(content):
            return True
    try:
        parsed = json.loads(text)
    except (ValueError, RecursionError):
        return False
    def inspect(item):
        if isinstance(item, dict):
            return any((sensitive_key(k) and not _placeholder(v)) or inspect(v) for k,v in item.items())
        if isinstance(item, list):
            return any(inspect(v) for v in item)
        return isinstance(item, str) and bool(_PEM.search(item) or _TOKEN.search(item))
    return inspect(parsed)


def _redact_url(match):
    raw = match.group(0)
    try:
        parts = urlsplit(raw)
        netloc = parts.netloc
        if '@' in netloc:
            netloc = REDACTED + '@' + netloc.rsplit('@', 1)[1]
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        has_secret = any(sensitive_key(unquote(k)) for k,_ in pairs)
        query = urlencode([(k, REDACTED if sensitive_key(unquote(k)) else v) for k,v in pairs]) if has_secret else parts.query
        fragment = parts.fragment
        if fragment and (parts.hostname in {'localhost', '127.0.0.1', '::1'} or
                         any(sensitive_key(k) for k,_ in parse_qsl(fragment)) or _TOKEN.search(fragment)):
            fragment = REDACTED
        if (netloc, query, fragment) == (parts.netloc, parts.query, parts.fragment):
            return raw
        return urlunsplit((parts.scheme, netloc, parts.path, query, fragment))
    except (ValueError, UnicodeError):
        return '[redacted-url]'


def _scrub_text(value):
    value = _PEM.sub(REDACTED, value)
    value = _TOKEN.sub(REDACTED, value)
    value = _URL.sub(_redact_url, value)
    # Preserve ordinary source/argv escaping; normalize only text that is masked.
    unnormalized = value
    # Quote-escaped partial JSON is common in native CLI error messages.
    for _ in range(3):
        normalized_quotes = value.replace('\\"', '"').replace("\\'", "'")
        if normalized_quotes == value:
            break
        value = normalized_quotes
    normalized = value
    def pair(match):
        try:
            key = json.loads(match.group(1).rsplit(':', 1)[0].strip())
        except ValueError:
            return match.group(0)
        return match.group(1) + json.dumps(REDACTED) if sensitive_key(key) else match.group(0)
    value = _JSON_PAIR.sub(pair, value)
    value = _QUOTED.sub(lambda m: m.group(1) + json.dumps(REDACTED), value)
    value = re.sub(r'(?i)(\b(?:authorization|proxy-authorization)\s*[:=]\s*)(?:Bearer|Basic|Token)\s+[^\s,;]+', r'\1'+REDACTED, value)
    value = re.sub(r'(?i)\b(bearer\s+)[A-Za-z0-9_.~+/=-]+', r'\1'+REDACTED, value)
    value = _BARE.sub(lambda m: m.group(1) + REDACTED, value)
    if value == normalized:
        value = unnormalized
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', value)


def redact_value(value):
    """Return JSON-compatible telemetry with credential fields removed recursively."""
    if isinstance(value, dict):
        return {k: REDACTED if sensitive_key(k) else redact_value(v) for k,v in value.items()}
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    if isinstance(value, tuple):
        return [redact_value(v) for v in value]
    if isinstance(value, str):
        return _scrub_text(value)
    return value


def redact_text(value):
    if not isinstance(value, str):
        raise TypeError('Public text must be a string')
    try:
        parsed = json.loads(value)
    except (ValueError, RecursionError):
        return _scrub_text(value)
    if isinstance(parsed, (dict, list)):
        redacted = redact_value(parsed)
        return value if redacted == parsed else json.dumps(redacted, ensure_ascii=False)
    return _scrub_text(value)