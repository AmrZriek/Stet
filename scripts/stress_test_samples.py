"""Stress test evaluation samples for Stet.

Defines a pool of 26 detailed multi-mode test scenarios covering various text formats
(emails, texts, code blocks, URLs, logs, markdown tables) and error types.
"""

TEST_SAMPLES = [
    # 1. Business Email
    {
        "id": "work_email",
        "format": "email",
        "input": "hi team, i wanted to follow up on the status of the project. basically, me and john was talking and we think that we should definately postpone the launch of the new feature until next week because the API is actting very unstable and has many bugs. let me know what you think. see http://status.company-internal.net/api/v1 for the live log."
    },
    # 2. Casual Chat
    {
        "id": "casual_chat",
        "format": "text",
        "input": "hey are you comming to the libary later? i cant find my keys, its so annoying. let me know if u see them or if you have any advise."
    },
    # 3. Technical Setup Log
    {
        "id": "tech_post",
        "format": "blog post",
        "input": "im currently setting up a new virtual environment using `python -m venv venv` and then running `source venv/bin/activate` but its raising a weird error. the error log is stored at `/var/log/stet_setup.log`. if you look at the script it has multiple issues: first, the directory path `/usr/local/bin` doesn't exist, second, the permissions are wrong. can you help me write a cleanup script? check http://github.com/stet-project/issues/102 for more info."
    },
    # 4. Social Media / Review
    {
        "id": "movie_review",
        "format": "social post",
        "input": "wow, that is definately the worst movie i have ever seen! the plot makes no sense and the acting was terrible... i dont understand how it got a 90% on rotten tomatoes. did anyone else feel the same or am i just trippin? let me know in the comments."
    },
    # 5. Dictation Notes
    {
        "id": "meeting_dictation",
        "format": "notes",
        "input": "so yeah basically the meeting is at 2 PM tomorrow, make sure to bring the slides for the Q3 review. also we need to discuss the budget cuts, um, about 10% across all departments. John will be there too."
    },
    # 6. Customer Support Ticket
    {
        "id": "support_ticket",
        "format": "ticket",
        "input": "hello support, i have a billing issue regarding my subscription. i was charged twice on my credit card on 2026-07-01 for $49.00 each. this is unacceptable. i tried to email billing@stetapp.io but got no reply. please fix this immediatelly and refund one of the charges."
    },
    # 7. Scientific/Academic Blurb
    {
        "id": "academic_blurb",
        "format": "academic",
        "input": "the researchers has conducted a series of tests to prove the efficacy of the vaccine. however, the results was highly inconsistent because of a contamination in the lab. we believe that further studies is absolutely vital to confirm these findings. the data shows that 34% of samples was affected."
    },
    # 8. Proper Noun & Acronym Casing
    {
        "id": "proper_nouns",
        "format": "post",
        "input": "yesterday i used github to host my code and AWS to deploy the backend. i also tried stet on windows 11. nvidia gpu acceleration was fully active because of cuda 12.4. that is much faster than running on cpu."
    },
    # 9. URL & Email Preservation
    {
        "id": "url_email_preservation",
        "format": "text",
        "input": "plz verify your account by going to https://www.secure-auth-service.com/login?user=amr&session=xyz. if you have any questions send a email to support@stet.ai and not to support@stet.com."
    },
    # 10. File Paths & Code Spans
    {
        "id": "file_paths_code",
        "format": "post",
        "input": "to fix this error, go to C:\\Users\\Administrator\\AppData\\Local\\Stet\\config.json and change `\"gpu_layers\": 99` to `\"gpu_layers\": 34`. running `python main.py --config=./config.json` will reload the backend."
    },
    # 11. Run-on Sentences & Semicolons
    {
        "id": "run_on_sentence",
        "format": "email",
        "input": "the report was finished on time but the manager did not read it until this morning and now he wants us to change everything which is impossible because we have a deadline in two hours."
    },
    # 12. Double Negatives & Modifiers
    {
        "id": "grammatical_flaws",
        "format": "post",
        "input": "we don't need no more updates to this system. having worked on it for months, the bug was finally found by me. also i couldn't barely hear the speaker during the webinar."
    },
    # 13. Double Words & Repetitions
    {
        "id": "repeated_words",
        "format": "text",
        "input": "the the project is going very very well but we still need to review the the budget details again again."
    },
    # 14. British vs. American English
    {
        "id": "dialect_spelling",
        "format": "blog post",
        "input": "the colour of the new design is great. i spent hours in the centre of london talking with my neighbour about this project. we also analysed some data."
    },
    # 15. Dialogue with Nested Quotes
    {
        "id": "nested_quotes",
        "format": "academic",
        "input": "he said \"she told me 'i won't do it' and left\". this shows the level of conflict."
    },
    # 16. Homophone Confusions
    {
        "id": "homophones",
        "format": "email",
        "input": "their going to the meeting to discuss there options. its going to take place in it's usual room over who's schedule is free."
    },
    # 17. Ellipsis & Dash Preservation
    {
        "id": "dashes_ellipses",
        "format": "text",
        "input": "well... we tried to install the package - it was version 2.1.0 - but it failed completely... maybe you can try next."
    },
    # 18. Unicode & Special Emoji
    {
        "id": "unicode_emoji",
        "format": "post",
        "input": "im going to buy some 🍎, 🍌, and 🍊. the price is 1500¥. this is for the party tonight 🎉! it will be awesome."
    },
    # 19. Abbreviations & Initialisms
    {
        "id": "acronyms_casing",
        "format": "notes",
        "input": "the ceo discussed the q3 goals with the vp of hr. they talked about the new policy on wfh. this starts on monday."
    },
    # 20. Code Comment formatting
    {
        "id": "code_comments",
        "format": "post",
        "input": "# TODO: we need to rewrite this function to prevent sql injection.\n# me and him should review the code before merge."
    },
    # 21. Lay / Lie Confusion
    {
        "id": "lay_lie",
        "format": "text",
        "input": "im going to lay down on the couch for an hour. the book was laying on the table. he lied the map flat."
    },
    # 22. Brackets & Parens Nested
    {
        "id": "brackets_parens",
        "format": "notes",
        "input": "the release (v1.1.0 [which was delayed]) contains many features. see the readme [stored in docs/]."
    },
    # 23. Markdown Table Preservation
    {
        "id": "markdown_table",
        "format": "notes",
        "input": "| Name | Cnt | Status |\n|---|---|---|\n| stet | 1 | ok |\n| llm | 2 | err |"
    },
    # 24. Empty and Whitespace Inputs
    {
        "id": "whitespace_only",
        "format": "text",
        "input": "   \n   "
    },
    # 25. Slang / Colloquialisms
    {
        "id": "colloquial_slang",
        "format": "text",
        "input": "bro this new app is fire ngl, u should check it out asap. its way better than the old stuff. im totally hyped."
    },
    # 26. Final Long Stress Test (Varying Scenarios)
    {
        "id": "final_long_stress_test",
        "format": "long post/email",
        "input": "so yeah basically, me and the dev team had a quick call yesterday morning regarding the stet app launch. we have some major blockkers on windows 11. first, the gpu acceleration is not starting up, nvidia drivers is throwing a error, check C:\\ProgramData\\Stet\\logs\\error.log for detail. i think we should definately postphone it. did you see the github issue? its at https://github.com/StetApp/Stet/issues/99. anyway, contact support@stetapp.io for more info. also, check out this code snippet to fix: `if config.gpu == True: load_cuda()` but its not working, actually it has a typo. we cant release this in colour or any other way if it doesnt compile. tell john i said hi and let me know if u have any advise."
    }
]
