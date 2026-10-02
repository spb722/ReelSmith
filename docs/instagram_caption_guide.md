# Instagram Caption Guide for Book Reels

How to write the Instagram caption for a finished reel. Rules checked September 2026. Instagram changes often, so re-check the hashtag limit and ranking advice every few months.

## 1. Start from the reel's own script

Don't write from memory. Open the project and read what the reel actually says:

```bash
python3 -c "import json;d=json.load(open('projects/<project>/metadata/final_story_plan.json'));print(d['story_title']);print(d['narration_script'])"
```

- Use `final_story_plan.json`, not `orchestrator_runs/story_agent_attempt_*.json`. The attempt files hold early drafts, and the final script can be different.
- To see the source book text (the chapter title and the original claims), look at the `"text"` fields in `metadata/analyzed_assets.json`.
- The caption must match the reel. Don't add facts, numbers or quotes the reel doesn't contain.

## 2. Platform rules (hard limits)

| Rule | Limit |
|---|---|
| Hashtags | **At most 5** per post or Reel. Instagram enforces this since December 2025. |
| Caption length | 2,200 characters at most. |
| Visible before "…more" | About the first 125 characters. This is the only part most people see. |

## 3. How reach works (why the caption is shaped this way)

- **Captions are read as search keywords.** Instagram uses caption text for search and recommendations, and it now counts more than hashtags. It also reads the words shown on screen in the video, so a caption that uses the same words as the subtitles strengthens the signal.
- **Saves and shares are strong ranking signals.** Ask for them directly.
- **Specific questions get comments.** A question people can answer in a few words beats a vague "thoughts?"
- **Broad hashtags add little.** Niche hashtags tell Instagram what the reel is about.

## 4. Caption template

```
[HOOK: 1 line, under ~125 chars. Reuse the reel's opening hook. Concrete image or number.] [1 emoji]

[NAME THE IDEA: 1–2 lines. Say the key concept by name, e.g. "That's pre-crastination".]

[THE STORY: 2–4 short lines. The example or study from the reel, in plain words.]

[THE TURN: 1–2 lines. The main takeaway in bold.]

✅ [ONE SMALL ACTION the viewer can take today.]

📌 Save this for [a specific moment when they'll need it], and send it to [a specific person, e.g. "the friend who…"].

💬 [A specific question that's easy to answer, tied to the reel's closing question.]

#niche1 #niche2 #niche3 #niche4 #bookreels
```

## 5. Writing rules

1. **The hook comes first.** Open with the most concrete, surprising detail in the reel (a price, a number, a scene), not a general statement.
2. **Write the search words into the sentences.** Include 3–5 phrases people might type into search (e.g. *time management*, *procrastination*, *to-do list*). Don't stuff them in; each one should read naturally.
3. **Name the concept.** If the reel teaches a named idea (e.g. *pre-crastination*, *action before motivation*), write the name out. People search for it.
4. **Short lines, lots of white space.** People read captions on phones, so keep lines to 1–2 sentences.
5. **Use a few emojis, not many.** About one per section, used as signposts. Never a string of them.
6. **Ask for one save and one share.** Make each request specific ("send it to the friend who clears their inbox first thing").
7. **End with a question.** Reuse or adapt the reel's final question so the reel and caption finish on the same note.
8. **Be honest about sources.** If the reel says a story "may not have really happened," the caption says so too. Only credit a book or author if the project files name it; otherwise check first.

## 6. Hashtag rules

- Use exactly **5**: 4 niche tags about the topic, plus `#bookreels`.
- Pick specific tags (`#precrastination`, `#timemanagement`) over broad ones (`#motivation`, `#love`, `#instagood`).
- Put them at the very end of the caption.
- Never repeat a tag, and don't use tags unrelated to the reel.

## 7. Checklist before posting

- [ ] Every claim in the caption appears in `final_story_plan.json`.
- [ ] The first line is under ~125 characters and would make someone tap "…more".
- [ ] The concept is named, and 3–5 search phrases appear naturally.
- [ ] It asks for one save and one share, each made specific.
- [ ] It ends with a specific question.
- [ ] Exactly 5 hashtags, all relevant, at the end.
- [ ] Under 2,200 characters in total.

## 8. Worked example: `you_get_what_you_measure1`

```
12 emails. 3 errands. 1 dentist booking. And the project that mattered? Untouched. 😮‍💨

That's not laziness. It's pre-crastination: the urge to start anything right away, even when it costs you more.

In one study, people had to carry one of two heavy buckets down a hallway. One bucket sat right beside them. The other sat much closer to the finish, so it was the easier carry. Almost everyone grabbed the near one and lugged it the whole way, just to start a few seconds sooner. 🪣

Your to-do list works the same way. You count tasks instead of weighing them. Being busy is not the same as getting things done.

✅ Before you start tomorrow, ask: what am I actually trying to win today?

📌 Save this for your next "busy but nothing done" day, and send it to the friend who clears their inbox first thing.

💬 What's the easy task you always grab first?

#productivitytips #precrastination #timemanagement #deepwork #bookreels
```

## Sources

- [Later: Instagram hashtags in 2026, win with the 5-tag limit](https://later.com/blog/ultimate-guide-to-using-instagram-hashtags/)
- [Instagram Reels Hashtags 2026: 5-Hashtag Limit](https://captionstudio.in/blog/instagram-hashtag-strategy-viral-reels.html)
- [Why Captions Matter More Than Hashtags on Instagram in 2026](https://lamplightcreatives.com/captions-vs-hashtags-instagram-2026/)
- [Instagram Reels Caption Best Practices for 2026](https://vsubtitle.com/instagram-reels-caption-best-practices/)
- [Instagram reel caption length (2026)](https://trustypost.ai/blog/instagram-reel-caption-length-2026-best-practices-examples-that-get-watched/)
