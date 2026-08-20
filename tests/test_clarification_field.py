"""Test that the clarification field works on SubagentResponse."""

from dendrophis.subagents.messages import SubagentResponse


def test_clarification_default_empty():
    """Clarification should default to empty list."""
    response = SubagentResponse(agent='code-writer', task_id='test', status='success', result={})
    assert response.clarification == []


def test_clarification_with_questions():
    """Clarification should hold a list of questions."""
    response = SubagentResponse(
        agent='code-writer',
        task_id='test2',
        status='needs_clarification',
        result={'changes': []},
        clarification=['Question 1?', 'Question 2?'],
    )
    assert response.clarification == ['Question 1?', 'Question 2?']
    assert response.status == 'needs_clarification'


if __name__ == '__main__':
    test_clarification_default_empty()
    test_clarification_with_questions()
    print('All checks passed!')
