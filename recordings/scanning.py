"""Reading a transcript for the things a person should look at.

Three kinds, in the order they matter:

    safety        something suggesting someone is at risk
    contact       a phone number or a social handle being passed
    off_platform  an arrangement to book or pay outside the app

Deliberately conservative. A flag costs a reviewer a minute; a false
accusation against a therapist costs them their standing, and an unnecessary
read of a therapy session costs the client their privacy. So the patterns ask
for specific, unambiguous things, and anything borderline is left alone.

What this is not: a decision. Nothing here suspends anyone or writes to a
practitioner's record. It puts an excerpt in a queue.
"""

import re

# Nigerian mobile numbers, spoken or written: 0803..., +234 803..., 234803...
# Eleven digits starting 070/080/081/090/091, or the +234 form of the same.
PHONE = re.compile(
    r'(?:(?:\+?234|0)\s?[789][01]\d(?:[\s-]?\d){7})',
)

# Digits spelled out, which is how a number is said aloud and how a
# transcript often renders it: "zero eight zero three ..."
SPOKEN_DIGITS = re.compile(
    r'(?:\b(?:zero|one|two|three|four|five|six|seven|eight|nine|oh)\b[\s,-]*){8,}',
    re.IGNORECASE,
)

HANDLE = re.compile(
    r'(?:^|\s)@[A-Za-z0-9._]{3,30}\b'
    r'|(?:instagram|ig|whatsapp|telegram|snapchat|tiktok)[\s:]+[@A-Za-z0-9._]{3,30}',
    re.IGNORECASE,
)

EMAIL = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')

# Phrases that only make sense as an arrangement to leave the platform. Each
# one is a whole proposal, not a single suspicious word: "pay" or "transfer"
# alone appear in ordinary talk about money worries.
OFF_PLATFORM = (
    # "book me directly", with or without naming the app afterwards.
    r'book\s+(?:me|with\s+me)\s+(?:directly|privately|outside|off)\b',
    r'book\s+(?:directly|outside|off)\s+(?:the\s+)?(?:app|platform)',
    r'(?:pay|send)\s+me\s+(?:directly|straight)',
    r'(?:outside|off)\s+(?:of\s+)?(?:the\s+)?(?:app|platform)',
    r'(?:cheaper|less)\s+if\s+(?:we|you)\s+(?:book|pay|go)\s+(?:direct|outside)',
    r'(?:don\'?t|do not|no need to)\s+(?:book|pay)\s+(?:through|on)\s+(?:the\s+)?app',
    r'my\s+(?:own\s+)?(?:private\s+)?(?:practice|clinic|studio)\s+(?:instead|rather)',
    r'(?:transfer|send)\s+(?:it\s+)?to\s+my\s+account',
)
OFF_PLATFORM_RE = re.compile('|'.join(OFF_PLATFORM), re.IGNORECASE)

# Risk of harm. Narrow on purpose: this queue is read by people, and burying
# a real disclosure under false positives is the failure that matters.
SAFETY = (
    r'\bkill\s+(?:myself|himself|herself|themselves)\b',
    r'\bend(?:ing)?\s+my\s+(?:own\s+)?life\b',
    r'\btake\s+my\s+own\s+life\b',
    r'\bsuicid(?:e|al)\b',
    r'\bhurt\s+(?:myself|the\s+(?:baby|children|kids))\b',
    r'\bno\s+reason\s+to\s+(?:live|go\s+on)\b',
)
SAFETY_RE = re.compile('|'.join(SAFETY), re.IGNORECASE)

EXCERPT_CHARS = 120


def scan(text):
    """Every finding in one transcript.

    Returns dicts of {kind, matched, excerpt, confidence}, ready to become
    Flag rows. An empty list is the usual and expected answer.
    """
    if not text:
        return []

    findings = []
    seen = set()

    for match in SAFETY_RE.finditer(text):
        findings.append(_finding('safety', match, text, 0.9))

    for pattern, confidence in ((PHONE, 0.95), (EMAIL, 0.9), (HANDLE, 0.7)):
        for match in pattern.finditer(text):
            findings.append(_finding('contact', match, text, confidence))

    for match in SPOKEN_DIGITS.finditer(text):
        # Lower: "two thousand and twenty four, five, six" is not a number
        # being passed, and a transcript cannot tell us which it was.
        findings.append(_finding('contact', match, text, 0.5))

    for match in OFF_PLATFORM_RE.finditer(text):
        findings.append(_finding('off_platform', match, text, 0.8))

    # One flag per place in the transcript: a phone number inside a sentence
    # about booking elsewhere is one thing to look at, not three.
    unique = []
    for finding in findings:
        key = (finding['kind'], finding['excerpt'])
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)
    return unique


def _finding(kind, match, text, confidence):
    start = max(0, match.start() - EXCERPT_CHARS // 2)
    end = min(len(text), match.end() + EXCERPT_CHARS // 2)
    excerpt = text[start:end].strip()
    if start > 0:
        excerpt = '…' + excerpt
    if end < len(text):
        excerpt = excerpt + '…'
    return {
        'kind': kind,
        'matched': match.group(0).strip()[:120],
        'excerpt': excerpt[:300],
        'confidence': confidence,
    }
