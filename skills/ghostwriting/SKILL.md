---
name: ghostwriting
description: Use when the user asks for, or the orchestrator proposes, text that another person will receive under the user's name -- a chat message, an email, a reply on a thread, a status to a team, an announcement, a note to a stakeholder -- "redáctame un mensaje para", "escríbele a", "respóndele a este hilo", "write a message to the team", "draft an email to", "reply to this thread for me".
---

# Ghostwriting

Ghostwriting is writing on the user's behalf: text another person receives as the user's own words, in the user's voice, with Gaia nowhere in it. It runs mid-flow. Upstream, the facts are settled -- a finding, a status, a decision already reached in the conversation; the skill never investigates them. Downstream, the user reads the draft and sends it or not, and the recipient acts on it without the conversation that produced it. A draft that reads as generated, or that is pitched to the wrong recipient, is paid by the user in front of someone else.

## Core principle

The register is `human-register`'s; what this skill adds is the recipient and the medium. The voice is constant -- the user's, natural and direct. The formality moves with the medium and the person: casual and direct in an instant message; somewhat more formal in an email, with a greeting, proper spacing between ideas and a close; adjusted to the recipient -- direct and short with an executive, fuller with someone who needs the context before the ask. How this user writes in each medium and to each recipient is in their preference rows; the skill applies those rows and does not restate them.

## Process

1. **Name the recipient and what they already know.** Their language, their time zone, their relation to the user, whether they saw the earlier thread. A message to someone outside the team carries its context in one line; a reply inside a thread carries none. A time is written in the standard the recipients share, never in the writer's zone, because a message to readers in several countries that favors one zone confuses the rest.
2. **Name the kind of message.** A notification (something happened), a request (someone does something, by when), a reply (a specific question, answered first), a status (where things stand, what comes next, what is needed). The kind fixes the order: what the recipient will act on comes first, the context after it.
3. **Write in the user's voice, with Gaia absent.** First person is the user. Nothing names agents, tooling, skills, contracts or how the text was produced -- the recipient is receiving the user's words, and a trace of the machinery makes them someone else's.
4. **Set the formality by the medium and the recipient.** Instant message: short lines, no greeting ritual, one ask. Email: a greeting, one paragraph per idea with a blank line between them, a close. Thread reply: the answer first, then the one sentence of context it needs. An executive: the decision or the number first, the reasoning in two lines, the ask in one.
5. **Apply the user's rows on top.** The look they give a medium, the people they address a certain way, the standard their teams share for time -- read from their preference rows for this surface, applied, and never written into this skill: the next user's messages would come out in this user's look.
6. **Hand it over; never send it.** The draft reaches the user in a copy block, ready to paste, and the user reads it before anything leaves. Sending through a mail or chat tool is a separate act with its own consent; the draft is never sent unasked.
7. **Judge it as the recipient.** Read the draft as the recipient would, on their device, without the conversation: the first line says what they need to know or do; nothing in it would tell them a machine wrote it (`human-register`, *What marks a machine*). The user, or a reader who did not write it, decides; the writer does not.

## Anti-patterns

- **The report in a message.** A status for a team written as the orchestrator's report to the user: identifiers, marks, the whole route. The recipient needed three lines and what to do.
- **One formality for every medium.** An email in chat register reads as curt; a chat message in email register reads as a form letter. The medium sets the distance.
- **Gaia in the room.** "I was asked to", "the agent found", a tool's name in a message to a stakeholder: the recipient now knows the words are not the user's.
- **The user's habit written as the rule.** A medium's look, a recipient's preferred bluntness, a team's time standard belong to the rows; written here they become every user's.
- **Sent instead of handed.** A message that left before the user read it cannot be taken back, and the user's name was on it.
