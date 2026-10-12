"""Frozen, conservative English report candidates; never gold target labels.

This small prototype recognizes explicit English findings in bounded clauses.
Script and language cues are rejection heuristics, not language identification.
Report content is used in memory only and is never included in returned values.
The canonical rule hash covers definitions and parsing policies; the notebook
separately verifies this module's source hash to identify the actual engine.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import unicodedata
from typing import Any

from .contracts import TARGETS


RULE_VERSION = "rsna-report-rules-v1.0.0"
ENGINE_POLICY = "conservative-english-clause-v1"
STATES = ("POSITIVE", "NEGATIVE", "UNKNOWN")
MAX_REPORT_CHARACTERS = 20_000
MAX_REPORT_CLAUSES = 256


@dataclass(frozen=True)
class TargetRule:
    """A readable target definition with no mutable compiled-regex cache."""

    target: str
    category: str
    mention: str
    abnormality: str
    normal: str


def _definitions() -> tuple[TargetRule, ...]:
    ligament = r"\b(?:tear(?:s|ing)?|torn|ruptur(?:e|ed)|disruption|discontinuity|avulsion|mucoid degeneration)\b"
    meniscus = r"\b(?:tear(?:s|ing)?|torn|ruptur(?:e|ed)|maceration|extrusion|degeneration|displaced fragment)\b"
    normal = r"\b(?:intact|normal|unremarkable|preserved)\b"
    oa = r"\b(?:oa|osteoarthritis|osteoarthrosis|degenerative joint disease|degenerative changes)\b"
    return (
        TargetRule("ACL", "structure", r"\b(?:acl|anterior cruciate ligament)\b", ligament, normal),
        TargetRule("MCL", "structure", r"\b(?:mcl|medial collateral ligament)\b", ligament + r"|\bsprain(?:ed)?\b", normal),
        TargetRule("Medial Meniscus", "structure", r"\bmedial menisc(?:us|al)\b", meniscus, normal),
        TargetRule("Lateral Meniscus", "structure", r"\blateral menisc(?:us|al)\b", meniscus, normal),
        TargetRule("Medial OA", "oa", r"\bmedial(?:\s+tibiofemoral)?\s+compartment\b|\bmedial(?=\s+(?:oa\b|osteoarthritis\b|osteoarthrosis\b))", oa, ""),
        TargetRule("Lateral OA", "oa", r"\blateral(?:\s+tibiofemoral)?\s+compartment\b|\blateral(?=\s+(?:oa\b|osteoarthritis\b|osteoarthrosis\b))", oa, ""),
        TargetRule("PF OA", "oa", r"\b(?:patellofemoral|pf)(?:\s+compartment)?\b", oa, ""),
        TargetRule("Effusion", "condition", r"\b(?:joint\s+)?effusions?\b", "", ""),
        TargetRule("Synovitis", "condition", r"\bsynovitis\b", "", ""),
        TargetRule("Baker's", "condition", r"\b(?:baker(?:['\u2019]s|s)?\s+cyst|popliteal cyst)\b", "", ""),
        TargetRule("Contusion", "condition", r"\b(?:(?:bone|osseous|marrow)\s+)?contusions?\b|\bbone bruis(?:e|ing)\b", "", ""),
        TargetRule("Fracture", "condition", r"\bfractures?\b", "", ""),
    )


def _cues() -> tuple[tuple[str, str], ...]:
    return (
        ("clause_separator", r"[.!?;\n\r,]+|\b(?:but|however|nevertheless)\b"),
        ("coordinate", r"\b(?:and|or|with|without)\b"),
        ("negative", r"\b(?:no|not|without|absent|absence of|negative for|not seen|not present|not identified|not demonstrated|not visualized|not detected|not evident|not appreciated|not torn|not ruptured|excluded)\b"),
        ("immediate_negative", r"\b(?:no|without|absence of|negative for)\s+(?:(?:evidence|signs?|of|in|a|an|the|any|acute|current|complete|partial|tear|tears|rupture|sprain|injury|degenerative|changes|joint|disease|oa|osteoarthritis|osteoarthrosis)\s+)*$"),
        ("post_negative", r"^\s*(?:(?:is|are|was|were|remains?|tear|tears|rupture|sprain|injury|oa|osteoarthritis|osteoarthrosis|degenerative|changes|has|demonstrates?)\s+)*(?:absent|not seen|not present|not identified|not demonstrated|not visualized|not detected|not evident|not appreciated|not torn|not ruptured|excluded|no\s+(?:tear|injury|rupture|sprain|abnormality))\b"),
        ("uncertain", r"\b(?:possible|possibly|possibility|probable|probably|likely|unlikely|suspect(?:ed)?|suspicious|suspicion|suggest(?:s|ive|ion)?|equivocal|questionable|may|might|could|versus|vs|differential|rule out|r/o|cannot exclude|can't exclude|not excluded|cannot be excluded|cannot be ruled out|not ruled out|concern(?:ing)? for|risk of|at risk)\b"),
        ("historical", r"\b(?:history of|historical|previous|previously|prior|remote|past|old healed|healed|after healing|resolved|status post|postoperative|post-operative|repaired)\b"),
        ("mention_only", r"\b(?:evaluate for|evaluation for|assess for|assessment for|query|check for|screen for|clinical concern|clinical suspicion|evaluated|mentioned|discussed)\b"),
        ("assertion", r"\b(?:there is|there are|demonstrates?|reveals?|shows?|identified|observed|visualized|seen|present|noted|evident)\b"),
        ("copula", r"\b(?:is|are)\b"),
        ("positive_qualifier", r"\b(?:small|moderate|large|mild|marked|severe|trace|significant|acute|subchondral|nondisplaced|displaced|comminuted|impacted)\b"),
        ("generic_abnormality", r"\b(?:abnormality|abnormalities|injury|injuries|tear|tears|rupture|sprain|degeneration)\b"),
        ("ignored_header", r"^(?:clinical history|clinical information|clinical indication|indication|history|technique|differential diagnosis|differential)\s*:\s*"),
        ("active_header", r"^(?:findings|impression|conclusion|results)\s*:\s*"),
        ("other_header", r"^([a-z][a-z ]{0,40})\s*:\s*"),
        ("header_boundary", r"\b(?:clinical history|clinical information|clinical indication|indication|history|technique|differential diagnosis|differential|findings|impression|conclusion|results)\s*:"),
        ("negated_normal", r"\b(?:not|no)\s+(?:intact|normal|unremarkable|preserved)\b"),
        ("other_structure", r"\b(?:pcl|posterior cruciate ligament|lcl|lateral collateral ligament|quadriceps|patellar tendon|skin|soft tissue)\b"),
        ("bone_contusion", r"\b(?:bone|osseous|marrow)\s+contusions?\b|\bbone bruis(?:e|ing)\b"),
        ("nonenglish_cue", r"\b(?:hai|hain|nahi|nahin|tha|thi|mein|aur|dard|bina)\b"),
    )


@dataclass(frozen=True)
class FrozenRuleSet:
    """Immutable definitions captured before agreement evaluation.

    verify() also detects deliberate object.__setattr__ tampering. It raises a
    fixed message and never prints the altered content.
    """

    version: str
    engine_policy: str
    rules: tuple[TargetRule, ...]
    cues: tuple[tuple[str, str], ...]
    normalization_policy: tuple[str, ...]
    scope_policy: tuple[str, ...]
    sha256: str

    def _canonical(self) -> bytes:
        payload = {
            "version": self.version, "engine_policy": self.engine_policy,
            "rules": [
                {"target": rule.target, "category": rule.category,
                 "mention": rule.mention, "abnormality": rule.abnormality,
                 "normal": rule.normal}
                for rule in self.rules
            ],
            "cues": [list(cue) for cue in self.cues],
            "normalization_policy": list(self.normalization_policy),
            "scope_policy": list(self.scope_policy),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")

    def verify(self) -> None:
        """Reject mutation and malformed definitions before any extraction."""
        try:
            valid = (
                type(self.rules) is tuple and all(type(rule) is TargetRule for rule in self.rules)
                and all(all(type(value) is str for value in (rule.target, rule.category, rule.mention, rule.abnormality, rule.normal)) for rule in self.rules)
                and tuple(rule.target for rule in self.rules) == TARGETS
                and type(self.cues) is tuple and all(type(cue) is tuple and len(cue) == 2 and all(type(value) is str for value in cue) for cue in self.cues)
                and type(self.normalization_policy) is tuple and type(self.scope_policy) is tuple
                and all(type(value) is str for value in (*self.normalization_policy, *self.scope_policy))
                and self.version == RULE_VERSION and self.engine_policy == ENGINE_POLICY
                and hashlib.sha256(self._canonical()).hexdigest() == self.sha256
            )
        except (TypeError, ValueError, AttributeError):
            valid = False
        if not valid:
            raise ValueError("frozen rule-set integrity check failed")

    def identity(self) -> dict[str, str]:
        self.verify()
        return {"version": self.version, "sha256": self.sha256, "engine_policy": self.engine_policy}


def freeze_rules() -> FrozenRuleSet:
    """Return a fresh immutable instance; no observed labels enter this API."""
    frozen = FrozenRuleSet(
        RULE_VERSION, ENGINE_POLICY, _definitions(), _cues(),
        ("Unicode NFKC", "casefold", "per-line whitespace collapse", "question mark adds uncertainty before clause splitting", "reject unexpected controls and non-Latin letters",
         "Latin script does not establish English; fixed rejection cues only"),
        ("bounded punctuation clauses", "history and indication sections ignored until an explicit findings section",
         "nearest structure with immediate coordinated negation; with and new copulas end inheritance", "comma after negation adds ambiguity", "explicit abnormality or absence required",
         "uncertainty and contradictions remain UNKNOWN", "no absent-term negative inference",
         "maximum source characters 20000", "maximum clauses 256", "maximum target context 160 characters"),
        "",
    )
    return FrozenRuleSet(
        frozen.version, frozen.engine_policy, frozen.rules, frozen.cues,
        frozen.normalization_policy, frozen.scope_policy,
        hashlib.sha256(frozen._canonical()).hexdigest(),
    )


def _unknown_result(frozen: FrozenRuleSet, status: str, reason: str) -> dict[str, Any]:
    return {
        "targets": {target: {"state": "UNKNOWN", "conflict": False, "reason_codes": (reason,)} for target in TARGETS},
        "report_status": status, "rule_identity": frozen.identity(),
    }


def _unsupported_characters(report: str) -> str | None:
    for char in report:
        category = unicodedata.category(char)
        if (category == "Cc" and char not in "\t\r\n") or category in {"Cf", "Cs", "Co", "Cn"} or char == "\ufffd":
            return "MALFORMED_UNICODE"
        if category.startswith("L") and "LATIN" not in unicodedata.name(char, ""):
            return "UNSUPPORTED_SCRIPT"
    return None


def _clauses(text: str, cues: dict[str, str]) -> list[tuple[str, bool]]:
    clauses: list[tuple[str, bool]] = []
    ignored_section = False
    text = re.sub(cues["header_boundary"], lambda match: "\n" + match.group(), text)
    for line in text.splitlines() or [text]:
        line = " ".join(line.split())
        line = line.replace("?", " possible;")
        ignored = re.match(cues["ignored_header"], line)
        active = re.match(cues["active_header"], line)
        other = re.match(cues["other_header"], line)
        if ignored:
            ignored_section = True
            line = line[ignored.end():]
        elif active:
            ignored_section = False
            line = line[active.end():]
        elif other:
            # Unknown headings are not reliably interpreted as current findings.
            ignored_section = True
            line = line[other.end():]
        ambiguous_comma = False
        previous_negative = False
        parts = re.split("(" + cues["clause_separator"] + ")", line)
        for index, fragment in enumerate(parts):
            if index % 2:
                ambiguous_comma = fragment == "," and previous_negative
                continue
            if fragment.strip():
                clause = fragment.strip() + (" possible" if ambiguous_comma and not re.match(cues["negative"], fragment.strip()) else "")
                clauses.append((clause, ignored_section))
                previous_negative = bool(re.search(cues["negative"], fragment))
    return clauses


def _target_contexts(clause: str, frozen: FrozenRuleSet, cues: dict[str, str]) -> list[tuple[TargetRule, str, bool]]:
    events = [(match.start(), match.end(), rule) for rule in frozen.rules for match in re.finditer(rule.mention, clause)]
    events.extend((match.start(), match.end(), None) for match in re.finditer(cues["other_structure"], clause))
    events.sort(key=lambda event: (event[0], event[1], event[2].target if event[2] else ""))
    if not events:
        return []
    inherited_negative = bool(re.search(cues["immediate_negative"], clause[:events[0][0]]))
    result = []
    for index, (start, end, rule) in enumerate(events):
        if rule is None:
            continue
        left, right = 0, len(clause)
        preceding_connector = ""
        if index:
            previous_end = events[index - 1][1]
            connectors = list(re.finditer(cues["coordinate"], clause[previous_end:start]))
            preceding_connector = connectors[-1].group() if connectors else ""
            if connectors:
                offset = connectors[-1].start() if preceding_connector == "without" else connectors[-1].end()
                left = previous_end + offset
            else:
                left = previous_end
        if index + 1 < len(events):
            next_start = events[index + 1][0]
            connector = re.search(cues["coordinate"], clause[end:next_start])
            right = end + connector.start() if connector else next_start
        context = clause[max(left, start - 80):min(right, end + 80)]
        shared_negative = (inherited_negative and preceding_connector not in {"with", "without"}
                           and not re.search(cues["assertion"], context) and not re.search(cues["copula"], context))
        # A coordinated 'no ACL or MCL tear' shares the final abnormality.
        if shared_negative and rule.category in {"structure", "oa"} and not re.search(rule.abnormality, context):
            if re.search(rule.abnormality, clause) and re.search(cues["coordinate"], clause):
                context += " " + ("tear" if rule.category == "structure" else "oa")
        result.append((rule, context, shared_negative))
    return result


def _negative_scope(context: str, rule: TargetRule, cues: dict[str, str]) -> tuple[bool, bool]:
    """Return established absence and ambiguous unrelated negation separately."""
    match = re.search(rule.mention, context)
    if not match:
        return False, False
    before, after = context[:match.start()], context[match.end():]
    established = bool(re.search(cues["immediate_negative"], before) or re.search(cues["post_negative"], after))
    return established, bool(re.search(cues["negative"], context) and not established)


def extract_report(report: Any, frozen: FrozenRuleSet, *, max_characters: int = MAX_REPORT_CHARACTERS,
                   max_clauses: int = MAX_REPORT_CLAUSES) -> dict[str, Any]:
    """Extract bounded tri-state candidates without returning input content.

    Smaller caller budgets are supported. Larger budgets are rejected so the
    canonical prototype remains bounded. Missing, malformed, unsupported and
    oversized reports produce UNKNOWN rather than partial-prefix predictions.
    """
    if type(frozen) is not FrozenRuleSet:
        raise TypeError("extract_report requires a frozen rule set")
    frozen.verify()
    if type(max_characters) is not int or not 1 <= max_characters <= MAX_REPORT_CHARACTERS:
        raise ValueError("invalid report character budget")
    if type(max_clauses) is not int or not 1 <= max_clauses <= MAX_REPORT_CLAUSES:
        raise ValueError("invalid report clause budget")
    if report is None:
        return _unknown_result(frozen, "MISSING", "MISSING_REPORT")
    if not isinstance(report, str):
        return _unknown_result(frozen, "INVALID_TYPE", "INVALID_REPORT_TYPE")
    if len(report) > max_characters:
        return _unknown_result(frozen, "CHARACTER_LIMIT", "CHARACTER_LIMIT")
    if not report.strip():
        return _unknown_result(frozen, "EMPTY", "EMPTY_REPORT")
    report = unicodedata.normalize("NFKC", report).casefold()
    if len(report) > max_characters:
        return _unknown_result(frozen, "CHARACTER_LIMIT", "CHARACTER_LIMIT")
    malformed = _unsupported_characters(report)
    if malformed:
        return _unknown_result(frozen, malformed, malformed)
    cues = dict(frozen.cues)
    if re.search(cues["nonenglish_cue"], report):
        return _unknown_result(frozen, "UNSUPPORTED_LANGUAGE_CUE", "UNSUPPORTED_LANGUAGE_CUE")
    clauses = _clauses(report, cues)
    if len(clauses) > max_clauses:
        return _unknown_result(frozen, "CLAUSE_LIMIT", "CLAUSE_LIMIT")
    evidence: dict[str, set[str]] = {target: set() for target in TARGETS}
    reasons: dict[str, set[str]] = {target: set() for target in TARGETS}
    for clause, ignored in clauses:
        for rule, context, inherited_negative in _target_contexts(clause, frozen, cues):
            target = rule.target
            if ignored or re.search(cues["historical"], clause) or re.search(cues["mention_only"], clause):
                reasons[target].add("NONCURRENT_OR_MENTION_ONLY")
                continue
            if re.search(cues["uncertain"], clause):
                reasons[target].add("UNCERTAIN_EVIDENCE")
                continue
            abnormal = bool(rule.abnormality and re.search(rule.abnormality, context))
            direct_negative, ambiguous_negative = _negative_scope(context, rule, cues)
            negative = inherited_negative or direct_negative
            if ambiguous_negative and not inherited_negative:
                reasons[target].add("AMBIGUOUS_NEGATION_SCOPE")
                continue
            normal = bool(rule.normal and re.search(rule.normal, context) and not re.search(cues["negated_normal"], context))
            if rule.category == "structure":
                if normal or (negative and (abnormal or re.search(cues["generic_abnormality"], context))):
                    evidence[target].add("NEGATIVE")
                if abnormal and not negative:
                    evidence[target].add("POSITIVE")
                if not normal and not abnormal:
                    reasons[target].add("MENTION_WITHOUT_EXPLICIT_FINDING")
            elif rule.category == "oa":
                if abnormal:
                    evidence[target].add("NEGATIVE" if negative else "POSITIVE")
                else:
                    reasons[target].add("COMPARTMENT_WITHOUT_EXPLICIT_OA")
            else:
                if negative:
                    evidence[target].add("NEGATIVE")
                elif target == "Contusion" and not re.search(cues["bone_contusion"], context):
                    reasons[target].add("TISSUE_UNSPECIFIED_OR_NONBONE")
                elif re.search(cues["assertion"], context) or re.search(cues["positive_qualifier"], context):
                    evidence[target].add("POSITIVE")
                else:
                    reasons[target].add("MENTION_WITHOUT_EXPLICIT_FINDING")
    result: dict[str, Any] = {"targets": {}, "report_status": "PROCESSED", "rule_identity": frozen.identity()}
    for target in TARGETS:
        states = evidence[target]
        conflict = states == {"POSITIVE", "NEGATIVE"}
        if conflict:
            state = "UNKNOWN"
            reasons[target].add("CONTRADICTORY_EVIDENCE")
        elif "UNCERTAIN_EVIDENCE" in reasons[target]:
            state = "UNKNOWN"
        elif len(states) == 1:
            state = next(iter(states))
            reasons[target].add("EXPLICIT_" + state)
        else:
            state = "UNKNOWN"
            if not reasons[target]:
                reasons[target].add("NO_TARGET_EVIDENCE")
        result["targets"][target] = {"state": state, "conflict": conflict, "reason_codes": tuple(sorted(reasons[target]))}
    return result
