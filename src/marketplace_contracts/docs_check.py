from __future__ import annotations

import html
import os
import re
import stat
import unicodedata
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

from . import VERSION
from .builder import CONTRACT_RELEASE, EXPECTED_SOURCE_IDENTITY, _validate_contract_release
from .io import MAX_PACKAGE_BYTES, load_json, load_json_bytes, read_bounded_file, sha256_bytes
from .model import ContractError, ValidationIssue, ValidationReport
from .reason_codes import REASON_CODES
from .validator import validate_document

_MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
_FENCED_CODE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`]*`")
_RAW_HTML = re.compile(
    r"(?is)<!--.*?-->|<\?.*?\?>|<!\[CDATA\[.*?\]\]>|<![A-Za-z][^>]*>|"
    r"<\s*/?\s*[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*?)?\s*/?>"
)
_ACTIVE_DOC = re.compile(
    r"(?is)<\s*/?\s*(?:script|iframe|object|embed|svg|math|img|link|style|meta|base)\b|"
    r"\bon[a-z][a-z0-9_-]*\s*=|\b(?:javascript|data|vbscript|file)\s*:"
)
_EVENT_ATTRIBUTE = re.compile(r"(?i)^on[a-z][a-z0-9_-]*$")
_SCRIPTABLE_URI = re.compile(r"(?i)^\s*(?:javascript|data|vbscript|file)\s*:")
_CONTENT_ROOT = re.compile(
    r"<div\b(?=[^>]*(?:\brole\s*=\s*['\"]main['\"]|\bid\s*=\s*['\"]main-content['\"]))[^>]*>",
    re.IGNORECASE,
)
_CONTENT_ROOT_CLOSE = re.compile(r"</\s*div\s*>", re.IGNORECASE)
_BUILD_STAMP = re.compile(
    r"\n<!--\nMkDocs version : 1\.6\.1\n"
    r"Build Date UTC : "
    r"(?P<timestamp>[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{6})?\+00:00)\n-->\n\Z"
)
_NORMALIZED_BUILD_STAMP = "\n<!--\nMkDocs version : 1.6.1\nBuild Date UTC : <normalized>\n-->\n"
_POST_CONTENT_SHA256 = {
    "404.html": {"47363427143ec00049a84fcc10ae0a724557a8478e01c56cd93240674e17258f"},
    "contracts/v1/index.html": {"9b53dfed7d93576971e02e1efd21df3df8ac2387c79849eaa091ede6556cd5cf"},
    "index.html": {
        "1a88925daedb2b8d53d203551cbc141a9f03ec21a417ada46f4fc20d8c110551",
        "534eea26e25b4b30614a9fc9251edab2bf9f7d43fb984ad7934a9cdf90e59d57",
    },
}
_CONTRACT_POST_CONTENT_SHA256 = "4809ed21211fd505283beb255fcd255eb73ee33abff8ac5fee8694c581b4f655"
_CONTRACT_PAGE = re.compile(r"^contracts/v1/[a-z0-9-]+/index\.html$")
_VOID_HTML_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
_CONTENT_TAGS = {
    "a",
    "blockquote",
    "br",
    "code",
    "dd",
    "dl",
    "dt",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}
_CONTENT_ATTRIBUTES = {
    "a": {"href", "title"},
    "code": {"class"},
    "h1": {"id", "style"},
    "h2": {"id", "style"},
    "h3": {"id", "style"},
    "h4": {"id", "style"},
    "h5": {"id", "style"},
    "h6": {"id", "style"},
    "img": {"alt", "src", "title"},
    "p": {"style"},
    "td": {"style"},
    "th": {"style"},
}
_SAFE_ALIGNMENT = re.compile(r"^text-align: (?:left|right|center);?$")
_EXCLUDED_SOURCE_PARTS = {"architecture", "validation"}
_MAX_DOC_SOURCE_BYTES = MAX_PACKAGE_BYTES
_MKDOCS_CONFIG_SHA256 = "6757e9f6ea36d887687c2cc28cc9e12809ad160463ffde5d2f4f38f46d7df083"
_EXAMPLES = {
    "docs/examples/v1/theme.json": "theme",
    "docs/examples/v1/widget.json": "widget",
    "docs/examples/v1/panel.json": "panel",
}
_REQUIRED_DOC_FRAGMENTS = {
    "docs/contracts/v1/index.md": [
        "172X-MKT-CONTRACTS-001",
        "private",
        "0.1.0",
        EXPECTED_SOURCE_IDENTITY,
        "does not prove publisher identity or package safety",
    ],
    "docs/contracts/v1/widget.md": [
        "host-bundled-source",
        "no Widget runtime code is downloaded",
        "exact compatible 172X Command build",
    ],
    "docs/contracts/v1/lifecycle-and-trust.md": [
        "accepted-unpublished",
        "not a safety guarantee",
        "No self-approval",
    ],
}
_FALSE_CLAIMS = [
    "the marketplace is publicly available",
    "install the Clock Widget from the catalog",
    "downloaded Widget code executes",
    "official Catppuccin package",
]
_PRIVATE_SITE_MARKERS = (
    "/Users/zbigniew/dev/code/172x-command",
    "src-tauri/",
    "tauri::command",
    "invoke(",
    "feat/intelligence-plugins-platform-support",
    "35e7bba0b9f48fc0130d22c3b211a3698203b288",
    "feat/wave-1-private-contracts",
    "a68a75464bf394df09de1e03b94f3b7075174e81",
)


def _decoded_text(value: str) -> str:
    decoded = value
    for _ in range(3):
        expanded = unquote(html.unescape(decoded))
        if expanded == decoded:
            break
        decoded = expanded
    return decoded


def _contains_disallowed_control(value: str, *, allow_markdown_spacing: bool) -> bool:
    permitted = {"\n", "\r", "\t"} if allow_markdown_spacing else set()
    return any(
        character not in permitted and unicodedata.category(character).startswith("C")
        for character in value
    )


class _GeneratedHtmlPolicyParser(HTMLParser):
    """Check the MkDocs-rendered content region without treating template scripts as authored docs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.content_div_depth = 0
        self.issue = False
        self.open_tags: list[str] = []
        self.saw_content = False

    @property
    def in_content(self) -> bool:
        return self.content_div_depth > 0

    def _check_attributes(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        allowed = _CONTENT_ATTRIBUTES.get(tag, set())
        for name, value in attributes:
            normalized_name = name.casefold()
            attribute_value = value or ""
            if _EVENT_ATTRIBUTE.fullmatch(normalized_name) or normalized_name not in allowed:
                self.issue = True
                continue
            if _contains_disallowed_control(attribute_value, allow_markdown_spacing=False):
                self.issue = True
                continue
            decoded = _decoded_text(attribute_value)
            if _SCRIPTABLE_URI.search(decoded):
                self.issue = True
            if normalized_name == "style" and not _SAFE_ALIGNMENT.fullmatch(decoded):
                self.issue = True

    def _check_content_tag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        if tag not in _CONTENT_TAGS:
            self.issue = True
            return
        self._check_attributes(tag, attributes)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.casefold()
        normalized_attributes = [(name.casefold(), value) for name, value in attrs]
        attribute_map = dict(normalized_attributes)
        if normalized_tag not in _VOID_HTML_TAGS:
            self.open_tags.append(normalized_tag)
        if not self.in_content and normalized_tag == "div" and (
            attribute_map.get("role") == "main" or attribute_map.get("id") == "main-content"
        ):
            if self.saw_content:
                self.issue = True
                return
            self.content_div_depth = 1
            self.saw_content = True
            return
        if not self.in_content:
            return
        if normalized_tag == "div":
            self.content_div_depth += 1
            self.issue = True
            return
        self._check_content_tag(normalized_tag, normalized_attributes)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.in_content:
            self._check_content_tag(tag.casefold(), [(name.casefold(), value) for name, value in attrs])

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        was_in_content = self.in_content
        if not self.open_tags or self.open_tags[-1] != normalized_tag:
            self.issue = True
            return
        self.open_tags.pop()
        if not was_in_content:
            return
        if normalized_tag == "div":
            self.content_div_depth -= 1
        elif normalized_tag not in _CONTENT_TAGS:
            self.issue = True

    def handle_comment(self, data: str) -> None:
        if self.in_content:
            self.issue = True

    def parse_bogus_comment(self, index: int, report: bool = True) -> int:
        self.issue = True
        return super().parse_bogus_comment(index, report)

    def handle_decl(self, decl: str) -> None:
        if self.in_content or self.saw_content:
            self.issue = True

    def handle_pi(self, data: str) -> None:
        if self.in_content:
            self.issue = True

    def unknown_decl(self, data: str) -> None:
        if self.in_content or self.saw_content:
            self.issue = True


def _docs_source_paths(repo_root: Path) -> tuple[list[Path], list[ValidationIssue]]:
    root = Path(os.path.abspath(repo_root))
    docs_root = root / "docs"
    config = root / "mkdocs.yml"
    issues: list[ValidationIssue] = []
    files: list[Path] = []

    def metadata(path: Path, target: str) -> os.stat_result | None:
        try:
            return path.lstat()
        except FileNotFoundError:
            issues.append(ValidationIssue("FILE_NOT_FOUND", target, "required documentation input is missing"))
        except OSError:
            issues.append(
                ValidationIssue("FILE_READ_FAILED", target, "documentation input metadata could not be read")
            )
        return None

    root_metadata = metadata(root, "docs")
    if root_metadata is None or stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        if root_metadata is not None:
            issues.append(
                ValidationIssue("FILE_TYPE_UNSAFE", "docs", "documentation repository root must be a real directory")
            )
        return [], issues
    config_metadata = metadata(config, "mkdocs.yml")
    if config_metadata is not None:
        if stat.S_ISLNK(config_metadata.st_mode) or not stat.S_ISREG(config_metadata.st_mode):
            issues.append(
                ValidationIssue("FILE_TYPE_UNSAFE", "mkdocs.yml", "documentation configuration must be a regular file")
            )
        else:
            files.append(config)
    docs_metadata = metadata(docs_root, "docs")
    if docs_metadata is None:
        return files, issues
    if stat.S_ISLNK(docs_metadata.st_mode) or not stat.S_ISDIR(docs_metadata.st_mode):
        issues.append(ValidationIssue("FILE_TYPE_UNSAFE", "docs", "documentation root must be a real directory"))
        return files, issues

    for relative in sorted(_EXAMPLES):
        example_metadata = metadata(root / relative, relative)
        if example_metadata is None:
            continue
        if stat.S_ISLNK(example_metadata.st_mode):
            issues.append(
                ValidationIssue("FILE_TYPE_UNSAFE", relative, "documentation inputs must not be symbolic links")
            )
        elif not stat.S_ISREG(example_metadata.st_mode):
            issues.append(
                ValidationIssue("FILE_TYPE_UNSAFE", relative, "documentation inputs must be regular files")
            )

    pending = [docs_root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as scanned:
                entries = sorted(scanned, key=lambda entry: entry.name)
        except OSError:
            target = directory.relative_to(root).as_posix()
            issues.append(
                ValidationIssue("FILE_READ_FAILED", target, "documentation directory could not be read safely")
            )
            continue
        for entry in entries:
            path = Path(entry.path)
            relative_docs = path.relative_to(docs_root)
            if relative_docs.parts[0] in _EXCLUDED_SOURCE_PARTS or relative_docs.as_posix() == "README.md":
                continue
            target = path.relative_to(root).as_posix()
            try:
                item_metadata = entry.stat(follow_symlinks=False)
            except OSError:
                issues.append(
                    ValidationIssue("FILE_READ_FAILED", target, "documentation input metadata could not be read")
                )
                continue
            if stat.S_ISLNK(item_metadata.st_mode):
                issues.append(
                    ValidationIssue("FILE_TYPE_UNSAFE", target, "documentation inputs must not be symbolic links")
                )
            elif stat.S_ISDIR(item_metadata.st_mode):
                pending.append(path)
            elif stat.S_ISREG(item_metadata.st_mode):
                files.append(path)
            else:
                issues.append(
                    ValidationIssue("FILE_TYPE_UNSAFE", target, "documentation inputs must be regular files")
                )
    return sorted(files), issues


def _read_docs_sources(repo_root: Path, paths: list[Path]) -> tuple[dict[Path, bytes], list[ValidationIssue]]:
    content: dict[Path, bytes] = {}
    issues: list[ValidationIssue] = []
    for path in paths:
        target = path.relative_to(repo_root).as_posix()
        try:
            content[path] = read_bounded_file(path, max_bytes=_MAX_DOC_SOURCE_BYTES, target=target)
        except ContractError as exc:
            issues.extend(exc.issues)
    return content, issues


def _active_doc_issue(text: str, path: str) -> ValidationIssue | None:
    if _contains_disallowed_control(text, allow_markdown_spacing=True):
        return ValidationIssue("DOC_ACTIVE_CONTENT", path, "documentation contains prohibited control content")
    scanned = _INLINE_CODE.sub("", _FENCED_CODE.sub("", text))
    decoded = _decoded_text(scanned)
    if _RAW_HTML.search(decoded) or _ACTIVE_DOC.search(decoded):
        return ValidationIssue("DOC_ACTIVE_CONTENT", path, "documentation contains raw or active content")
    return None


def _normalize_build_stamp(tail: str) -> str:
    match = _BUILD_STAMP.search(tail)
    if match is None:
        return tail
    timestamp = match.group("timestamp")
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return tail
    if str(parsed) != timestamp:
        return tail
    return tail[: match.start()] + _NORMALIZED_BUILD_STAMP


def _generated_html_issue(text: str, path: str) -> ValidationIssue | None:
    if _contains_disallowed_control(text, allow_markdown_spacing=True):
        return ValidationIssue("DOC_ACTIVE_CONTENT", path, "generated HTML contains prohibited control content")
    parser = _GeneratedHtmlPolicyParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception:  # HTMLParser errors must remain sanitized and fail closed.
        return ValidationIssue("DOC_ACTIVE_CONTENT", path, "generated HTML could not be checked safely")
    expected_tail_hashes = set(_POST_CONTENT_SHA256.get(path, set()))
    if _CONTRACT_PAGE.fullmatch(path):
        expected_tail_hashes.add(_CONTRACT_POST_CONTENT_SHA256)
    content_root = _CONTENT_ROOT.search(text)
    content_close = _CONTENT_ROOT_CLOSE.search(text, content_root.end()) if content_root else None
    tail_hash = None
    if content_close:
        tail = text[content_close.end() :]
        normalized_tail = _normalize_build_stamp(tail)
        tail_hash = sha256_bytes(normalized_tail.encode("utf-8"))
    if (
        not parser.saw_content
        or parser.issue
        or parser.open_tags
        or tail_hash not in expected_tail_hashes
    ):
        return ValidationIssue("DOC_ACTIVE_CONTENT", path, "generated HTML contains raw or active content")
    return None


def _check_links(repo_root: Path, pages: dict[Path, str]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for page, text in pages.items():
        for match in _MARKDOWN_LINK.finditer(text):
            destination = match.group(1).strip().split("#", 1)[0]
            if not destination or destination.startswith(("http://", "https://", "mailto:", "#")):
                continue
            resolved = (page.parent / destination).resolve()
            if not resolved.is_relative_to(repo_root.resolve()) or not resolved.exists():
                issues.append(
                    ValidationIssue(
                        "DOC_LINK_BROKEN",
                        page.relative_to(repo_root).as_posix(),
                        "internal documentation link target is missing",
                    )
                )
    return issues


def validate_docs(repo_root: Path) -> ValidationReport:
    issues: list[ValidationIssue] = []
    repo_root = Path(os.path.abspath(repo_root))
    source_paths, source_path_issues = _docs_source_paths(repo_root)
    issues.extend(source_path_issues)
    if issues:
        return ValidationReport("docs", tuple(sorted(set(issues), key=lambda item: (item.path, item.code, item.message))))
    source_bytes, source_read_issues = _read_docs_sources(repo_root, source_paths)
    issues.extend(source_read_issues)
    if issues:
        return ValidationReport("docs", tuple(sorted(set(issues), key=lambda item: (item.path, item.code, item.message))))
    config_path = repo_root / "mkdocs.yml"
    if sha256_bytes(source_bytes[config_path]) != _MKDOCS_CONFIG_SHA256:
        issues.append(
            ValidationIssue("DOC_SYNC_FAILED", "mkdocs.yml", "documentation configuration differs from v1")
        )
    contract = load_json(repo_root / CONTRACT_RELEASE, repo_root=repo_root)
    issues.extend(_validate_contract_release(repo_root, contract))
    if isinstance(contract, dict) and contract.get("validatorVersion") != VERSION:
        issues.append(ValidationIssue("CONTRACT_SYNC_FAILED", CONTRACT_RELEASE, "validator version differs"))
    reason_registry = load_json(repo_root / "contracts/v1/reason-codes.json", repo_root=repo_root)
    if not isinstance(reason_registry, dict) or set(reason_registry.get("codes", [])) != REASON_CODES:
        issues.append(
            ValidationIssue("CONTRACT_SYNC_FAILED", "contracts/v1/reason-codes.json", "reason-code registry differs")
        )
    for relative, kind in sorted(_EXAMPLES.items()):
        path = repo_root / relative
        if path not in source_bytes:
            issues.append(
                ValidationIssue("FILE_NOT_FOUND", relative, "required documentation example is missing")
            )
            continue
        try:
            example = load_json_bytes(source_bytes[path], target=relative)
        except ContractError as exc:
            issues.extend(exc.issues)
            continue
        issues.extend(validate_document(repo_root, kind, example, target=relative).issues)
    pages: dict[Path, str] = {}
    for path, content in source_bytes.items():
        if path.suffix.casefold() != ".md":
            continue
        relative = path.relative_to(repo_root).as_posix()
        try:
            pages[path] = content.decode("utf-8")
        except UnicodeDecodeError:
            issues.append(ValidationIssue("DOC_ACTIVE_CONTENT", relative, "documentation must be valid UTF-8 text"))
    for relative, fragments in sorted(_REQUIRED_DOC_FRAGMENTS.items()):
        path = repo_root / relative
        if path not in pages:
            issues.append(ValidationIssue("FILE_NOT_FOUND", relative, "required developer documentation is missing"))
            continue
        text = pages[path]
        normalized = " ".join(text.split())
        for fragment in fragments:
            if fragment not in normalized:
                issues.append(ValidationIssue("DOC_SYNC_FAILED", relative, "required contract truth is missing"))
    docs_text_parts: list[str] = []
    for page, text in pages.items():
        relative = page.relative_to(repo_root).as_posix()
        docs_text_parts.append(text)
        active_issue = _active_doc_issue(text, relative)
        if active_issue:
            issues.append(active_issue)
    docs_text = "\n".join(docs_text_parts)
    for claim in _FALSE_CLAIMS:
        if claim.casefold() in docs_text.casefold():
            issues.append(ValidationIssue("DOC_FALSE_CLAIM", "docs", "documentation contains a prohibited claim"))
    issues.extend(_check_links(repo_root, pages))
    return ValidationReport("docs", tuple(sorted(set(issues), key=lambda item: (item.path, item.code, item.message))))


def validate_site_output(site_root: Path) -> ValidationReport:
    issues: list[ValidationIssue] = []
    if not site_root.is_dir() or site_root.is_symlink():
        return ValidationReport(
            "generated-site",
            (ValidationIssue("FILE_TYPE_UNSAFE", "generated-site", "site output must be a regular directory tree"),),
        )
    for item in sorted(site_root.rglob("*")):
        relative = item.relative_to(site_root).as_posix()
        metadata = item.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
            issues.append(
                ValidationIssue("FILE_TYPE_UNSAFE", relative, "site output entries must be regular files or directories")
            )
            continue
        if relative.split("/", 1)[0] in _EXCLUDED_SOURCE_PARTS:
            issues.append(
                ValidationIssue("DOC_SYNC_FAILED", relative, "review-only documentation was emitted into the site")
            )
            continue
        if not stat.S_ISREG(metadata.st_mode):
            continue
        try:
            content = read_bounded_file(item, max_bytes=4_194_304, target=relative)
        except ContractError as exc:
            issues.extend(exc.issues)
            continue
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError:
            if item.suffix.casefold() == ".html":
                issues.append(
                    ValidationIssue("DOC_ACTIVE_CONTENT", relative, "generated HTML must be valid UTF-8 text")
                )
            decoded = content.decode("utf-8", errors="ignore")
        if any(marker.casefold() in decoded.casefold() for marker in _PRIVATE_SITE_MARKERS):
            issues.append(
                ValidationIssue("DOC_SYNC_FAILED", relative, "generated site contains a private implementation marker")
            )
        if item.suffix.casefold() == ".html":
            active_issue = _generated_html_issue(decoded, relative)
            if active_issue:
                issues.append(active_issue)
    return ValidationReport(
        "generated-site",
        tuple(sorted(set(issues), key=lambda item: (item.path, item.code, item.message))),
    )
