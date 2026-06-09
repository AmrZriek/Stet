from stet.core.text_utils import contains_meta_commentary


def test_legitimate_questions_accepted():
    # Normal corrected questions should NOT be flagged as meta commentary
    assert not contains_meta_commentary("What time is it?")
    assert not contains_meta_commentary("Did you go to the store?")
    assert not contains_meta_commentary("How are you today?")
    assert not contains_meta_commentary("Is this sentence grammatically correct?")


def test_conversational_meta_questions_rejected():
    # Assistant clarification meta questions SHOULD be flagged as meta commentary
    assert contains_meta_commentary("Is there anything else I can help you with?")
    assert contains_meta_commentary("Does this look correct?")
    assert contains_meta_commentary("Let me know if you need anything else?")
    assert contains_meta_commentary("Would you like me to make any other changes?")
    assert contains_meta_commentary("Is this what you meant?")


def test_other_conversational_patterns_rejected():
    # Standard conversational preambles and structures
    assert contains_meta_commentary("Sure! Here is the corrected text:")
    assert contains_meta_commentary("I have corrected the spelling mistakes.")
    assert contains_meta_commentary("In my opinion, this version is better.")
