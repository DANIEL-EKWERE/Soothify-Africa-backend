"""Turning what somebody said in the questionnaire into what to show them.

The questionnaire speaks about the person -- "carrying heavy stress",
"restless sleep", "inner calm & peace of mind". The library speaks about
content -- calm, sleep, focus, breathwork, stories. This is the one place
the two vocabularies meet, so neither has to know about the other.

A map rather than a tag on every item, for now. The five concerns line up
almost exactly with the five seeded categories, and a map works the moment
it ships; per-item tagging is more precise but only earns its keep once the
library is large enough that a whole category is too blunt an answer. The
endpoint's contract is the same either way, so moving to tags later changes
nothing the app can see.
"""

#: What somebody is carrying -> the categories that speak to it.
#: Keys are the app's own option values (see kyc_question.dart).
CONCERNS = {
    'stress': ('calm', 'breathe'),
    'anxiety': ('breathe', 'calm'),
    'sleep_disorder': ('sleep', 'stories'),
    'depression': ('calm', 'focus'),
    # Somebody carrying nothing in particular. Deliberately the widest
    # answer rather than no answer: a blank Home is the worst first screen.
    'exploring': ('calm', 'sleep', 'focus', 'breathe', 'stories'),
}

#: What they hope to cultivate -> the same.
GOALS = {
    'clarity': ('focus', 'breathe'),
    'relationships': ('calm', 'stories'),
    'boundaries': ('calm', 'focus'),
    'peace': ('calm', 'sleep'),
    # "Something else" says only that the five did not fit, which is not
    # enough to narrow anything. Left out on purpose.
}


def categories_for(answers):
    """The category ids a set of questionnaire answers points at.

    `answers` is the stored shape: {question id: [chosen values]}. Anything
    unrecognised -- a question we have retired, an option added to the app
    before it was added here -- is skipped rather than raising, because a
    recommendation is not worth a 500.

    Returns them in the order they were first implied, so the strongest
    signal leads; an empty result means "we have nothing to go on", which
    the caller answers with the ordinary list.
    """
    ordered = []
    for question, table in (('concerns', CONCERNS), ('goals', GOALS)):
        for value in _values(answers.get(question)):
            for key in table.get(value, ()):
                if key not in ordered:
                    ordered.append(key)
    return ordered


def _values(raw):
    """One answer or several, as a list of strings either way."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        return [str(v) for v in raw]
    return [str(raw)]
