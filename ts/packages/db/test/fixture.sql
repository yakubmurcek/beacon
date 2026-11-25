-- The round-trip fixture: what scripts/demo.py --many --long writes into a fresh hub (made-up tasks and
-- decisions), then hand-written rows in every other table with the values a copy can get wrong: NULL next to '',
-- emoji, backslashes, tabs, newlines, a literal \N, floats with all 17 digits, integers past 2^53, duplicate rows
-- in the tables without a key, and AUTOINCREMENT marks above the highest id. Applied after the hub's own schema.
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (1, 'snow-game', '100 players try the new level this week', '## Goal
More players stay past the first level.

## Done-test
- Roblox analytics shows 100 first-time players on level 2.

## Steps
1. 🤖 Ship the level behind a flag.
2. 🤖 Post three short clips.
3. 👤 Alex: pick the clip that goes first.', 'doing', 1, NULL, '[]', NULL, 1795604733.453009, 1795604733.6189642, NULL, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (2, 'habit-app', 'First TestFlight build reaches 5 testers', '## Goal
Real people use the app before the store launch.

## Done-test
- 5 testers installed the build.

## Steps
1. 🤖 Build and upload.
2. 👤 Alex: send the invite to 5 friends.', 'todo', 2, NULL, '[]', NULL, 1795604733.4598432, 1795604733.4598432, NULL, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (3, 'weddings', 'The second couple''s site is live', '## Goal
A second paying couple.

## Done-test
- Their site opens on their own domain.

## Steps
1. 🤖 Copy the template.
2. 🤖 Deploy.', 'todo', 2, NULL, '[]', NULL, 1795604733.4621158, 1795604733.4621158, NULL, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (4, 'coaching', 'The booking page takes a first lesson', '## Goal
The first paid lesson booked without a message.

## Done-test
- One booking arrives through the page.

## Steps
1. 🤖 Add the calendar.', 'todo', 3, NULL, '[]', NULL, 1795604733.464453, 1795604733.464453, NULL, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (5, 'beacon', 'The Machines page loads in under a second', '## Goal
The hub stays fast.

## Done-test
- The page paints in under 1 s on a phone.

## Steps
1. 🤖 Cache the slow read.', 'done', 3, NULL, '[]', NULL, 1795604733.4670603, 1795604733.4703484, 1795604733.4703484, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (6, 'snow-game', 'SubscriptionCheckoutConfirmationFlowControllerV2 ships to 5 users', '## Goal
Rename the checkout flow to SubscriptionCheckoutConfirmationFlowControllerV2 everywhere

## Done-test
- Rename the checkout flow to SubscriptionCheckoutConfirmationFlowControllerV2 everywhere

## Steps
1. 🤖 Rename the checkout flow to SubscriptionCheckoutConfirmationFlowControllerV2 everywhere', 'todo', 1, NULL, '[]', NULL, 1795604733.4744947, 1795604733.4744947, NULL, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (7, 'habit-app', 'Landing page for the second couple loads under one second on phones', '## Goal
The landing page for the second couple loads in under a second on a slow phone, with the gallery, the map, the RSVP form and the countdown all visible above the fold and nothing jumping while fonts load

## Done-test
- The landing page for the second couple loads in under a second on a slow phone, with the gallery, the map, the RSVP form and the countdown all visible above the fold and nothing jumping while fonts load

## Steps
1. 🤖 The landing page for the second couple loads in under a second on a slow phone, with the gallery, the map, the RSVP form and the countdown all visible above the fold and nothing jumping while fonts load', 'todo', 1, NULL, '[]', NULL, 1795604733.4771466, 1795604733.4771466, NULL, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (8, 'weddings', 'NotificationPreferencesSettingsPanelComponent works in all 3 apps', '## Goal
Fix /Users/someone/Projects/very-long-project-name/src/components/settings/NotificationPreferences.tsx

## Done-test
- Fix /Users/someone/Projects/very-long-project-name/src/components/settings/NotificationPreferences.tsx

## Steps
1. 🤖 Fix /Users/someone/Projects/very-long-project-name/src/components/settings/NotificationPreferences.tsx', 'todo', 1, NULL, '[]', NULL, 1795604733.4799097, 1795604733.4799097, NULL, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (9, 'snow-game', 'Made-up task 1 for snow-game', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'todo', 1, NULL, '[]', NULL, 1795604733.482449, 1795604733.482449, NULL, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (10, 'habit-app', 'Made-up task 2 for habit-app', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'doing', 2, NULL, '[]', NULL, 1795604733.48484, 1795604733.4876058, NULL, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (11, 'weddings', 'Made-up task 3 for weddings', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'done', 3, NULL, '[]', NULL, 1795604733.4897854, 1795604733.4925728, 1795604733.4925728, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (12, 'coaching', 'Made-up task 4 for coaching', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'todo', 1, NULL, '[]', NULL, 1795604733.496088, 1795604733.496088, NULL, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (13, 'beacon', 'Made-up task 5 for beacon', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'doing', 2, NULL, '[]', NULL, 1795604733.4982448, 1795604733.5010588, NULL, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (14, 'a-very-long-project-name-here', 'Made-up task 6 for a-very-long-project-name-here', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'done', 3, NULL, '[]', NULL, 1795604733.503204, 1795604733.5060067, 1795604733.5060067, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (15, 'snow-game', 'Made-up task 7 for snow-game', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'todo', 1, NULL, '[]', NULL, 1795604733.5096014, 1795604733.5096014, NULL, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (16, 'habit-app', 'Made-up task 8 for habit-app', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'doing', 2, NULL, '[]', NULL, 1795604733.5118234, 1795604733.5147297, NULL, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (17, 'weddings', 'Made-up task 9 for weddings', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'done', 3, NULL, '[]', NULL, 1795604733.51691, 1795604733.5198464, 1795604733.5198464, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (18, 'coaching', 'Made-up task 10 for coaching', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'todo', 1, NULL, '[]', NULL, 1795604733.523331, 1795604733.523331, NULL, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (19, 'beacon', 'Made-up task 11 for beacon', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'doing', 2, NULL, '[]', NULL, 1795604733.5255902, 1795604733.5283926, NULL, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (20, 'a-very-long-project-name-here', 'Made-up task 12 for a-very-long-project-name-here', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'done', 3, NULL, '[]', NULL, 1795604733.5305743, 1795604733.5334172, 1795604733.5334172, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (21, 'snow-game', 'Made-up task 13 for snow-game', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'todo', 1, NULL, '[]', NULL, 1795604733.5369377, 1795604733.5369377, NULL, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (22, 'habit-app', 'Made-up task 14 for habit-app', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'doing', 2, NULL, '[]', NULL, 1795604733.5391033, 1795604733.5418644, NULL, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (23, 'weddings', 'Made-up task 15 for weddings', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'done', 3, NULL, '[]', NULL, 1795604733.5440593, 1795604733.5468202, 1795604733.5468202, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (24, 'coaching', 'Made-up task 16 for coaching', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'todo', 1, NULL, '[]', NULL, 1795604733.5502625, 1795604733.5502625, NULL, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (25, 'beacon', 'Made-up task 17 for beacon', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'doing', 2, NULL, '[]', NULL, 1795604733.5524158, 1795604733.55524, NULL, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (26, 'a-very-long-project-name-here', 'Made-up task 18 for a-very-long-project-name-here', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'done', 3, NULL, '[]', NULL, 1795604733.5573702, 1795604733.5601332, 1795604733.5601332, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (27, 'snow-game', 'Made-up task 19 for snow-game', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'todo', 1, NULL, '[]', NULL, 1795604733.5636907, 1795604733.5636907, NULL, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (28, 'habit-app', 'Made-up task 20 for habit-app', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'doing', 2, NULL, '[]', NULL, 1795604733.5658507, 1795604733.568646, NULL, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (29, 'weddings', 'Made-up task 21 for weddings', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'done', 3, NULL, '[]', NULL, 1795604733.5708952, 1795604733.5736914, 1795604733.5736914, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (30, 'coaching', 'Made-up task 22 for coaching', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'todo', 1, NULL, '[]', NULL, 1795604733.5773022, 1795604733.5773022, NULL, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (31, 'beacon', 'Made-up task 23 for beacon', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'doing', 2, NULL, '[]', NULL, 1795604733.5794888, 1795604733.582268, NULL, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (32, 'a-very-long-project-name-here', 'Made-up task 24 for a-very-long-project-name-here', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'done', 3, NULL, '[]', NULL, 1795604733.5844975, 1795604733.5873017, 1795604733.5873017, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (33, 'snow-game', 'Made-up task 25 for snow-game', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'todo', 1, NULL, '[]', NULL, 1795604733.5908365, 1795604733.5908365, NULL, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (34, 'habit-app', 'Made-up task 26 for habit-app', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'doing', 2, NULL, '[]', NULL, 1795604733.5929923, 1795604733.5958867, NULL, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (35, 'weddings', 'Made-up task 27 for weddings', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'done', 3, NULL, '[]', NULL, 1795604733.5980098, 1795604733.6008658, 1795604733.6008658, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (36, 'coaching', 'Made-up task 28 for coaching', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'todo', 1, NULL, '[]', NULL, 1795604733.6043193, 1795604733.6043193, NULL, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (37, 'beacon', 'Made-up task 29 for beacon', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'doing', 2, NULL, '[]', NULL, 1795604733.6064649, 1795604733.609388, NULL, NULL);
INSERT INTO tasks (id, project, title, body, status, priority, due, tags, source, created_at, updated_at, closed_at, step) VALUES (38, 'a-very-long-project-name-here', 'Made-up task 30 for a-very-long-project-name-here', '## Goal
Fill the page.

## Done-test
- Shows.

## Steps
1. 🤖 Nothing.', 'done', 3, NULL, '[]', NULL, 1795604733.6115289, 1795604733.6143708, 1795604733.6143708, NULL);
INSERT INTO task_comments (id, task_id, at, author, text) VALUES (1, 1, 1795604733.6189642, 'agent', '👉 Level 2 is behind the flag; clips are drafted.');
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (1, 'snow-game', 'Which clip goes out first?', '', '[{"label": "The snowball fight"}, {"label": "The speed run"}, {"label": "The new map"}]', 'The snowball fight', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.622993, 1795604733.627221, 'kind "decision" is not on the low-risk list', 0, 1, NULL, NULL, '[]', NULL, 1795604733.6272113, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (2, 'habit-app', 'Price it at $2.99 a month or $19.99 a year?', '', '[{"label": "$19.99 a year"}, {"label": "$2.99 a month"}]', '$19.99 a year', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 2, NULL, 1795604733.6283226, 1795604733.6324246, 'risk word "price" (pay)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.6324244, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (3, 'weddings', 'Send the offer to the second couple today?', '', '[{"label": "Yes, send it"}]', 'Yes, send it', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 3, NULL, 1795604733.633723, 1795604733.638831, 'risk word "offer" (pay)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.6388307, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (4, 'snow-game', 'Rename the checkout flow to SubscriptionCheckoutConfirmationFlowControllerV2 everywhere?', '', '[{"label": "SubscriptionCheckoutConfirmationFlowControllerV2 ships to 5 users"}, {"label": "No"}]', 'SubscriptionCheckoutConfirmationFlowControllerV2 ships to 5 users', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.640164, 1795604733.644268, 'risk word "subscri" (pay)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.644268, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (5, 'snow-game', 'The landing page for the second couple loads in under a second on a slow phone, with the gallery, the map, the RSVP form and the countdown all visible above the fold and nothing jumping while fonts load?', '', '[{"label": "Landing page for the second couple loads under one second on phones"}, {"label": "No"}]', 'Landing page for the second couple loads under one second on phones', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.6459856, 1795604733.650087, 'risk word "form" (send)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.650087, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (6, 'snow-game', 'Fix /Users/someone/Projects/very-long-project-name/src/components/settings/NotificationPreferences.tsx?', '', '[{"label": "NotificationPreferencesSettingsPanelComponent works in all 3 apps"}, {"label": "No"}]', 'NotificationPreferencesSettingsPanelComponent works in all 3 apps', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.651473, 1795604733.6554515, 'risk word "users" (people)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.6554513, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (7, 'snow-game', 'Made-up decision 1: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.6566477, 1795604733.6606402, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.66064, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (8, 'snow-game', 'Made-up decision 2: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.6616845, 1795604733.6657023, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.665702, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (9, 'snow-game', 'Made-up decision 3: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.6667557, 1795604733.6707702, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.6707702, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (10, 'snow-game', 'Made-up decision 4: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.6722023, 1795604733.6771417, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.6771417, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (11, 'snow-game', 'Made-up decision 5: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.6795905, 1795604733.6835706, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.6835704, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (12, 'snow-game', 'Made-up decision 6: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.6847768, 1795604733.6888099, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.6888096, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (13, 'snow-game', 'Made-up decision 7: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.6898768, 1795604733.6949072, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.694907, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (14, 'snow-game', 'Made-up decision 8: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.696186, 1795604733.7003438, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.7003436, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (15, 'snow-game', 'Made-up decision 9: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.70142, 1795604733.7054815, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.7054813, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (16, 'snow-game', 'Made-up decision 10: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.7067444, 1795604733.711171, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.7111707, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (17, 'snow-game', 'Made-up decision 11: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.7124333, 1795604733.716713, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.7167127, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (18, 'snow-game', 'Made-up decision 12: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.7178094, 1795604733.7222779, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.7222776, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (19, 'snow-game', 'Made-up decision 13: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.7233875, 1795604733.7274466, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.7274463, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (20, 'snow-game', 'Made-up decision 14: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.7288358, 1795604733.7328835, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.7328832, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (21, 'snow-game', 'Made-up decision 15: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.7341242, 1795604733.7382646, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.7382643, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (22, 'snow-game', 'Made-up decision 16: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.7394063, 1795604733.7434194, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.7434192, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (23, 'snow-game', 'Made-up decision 17: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.7447515, 1795604733.7488463, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.7488463, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (24, 'snow-game', 'Made-up decision 18: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.750067, 1795604733.7541223, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.754122, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (25, 'snow-game', 'Made-up decision 19: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.7553716, 1795604733.7595866, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.7595863, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (26, 'snow-game', 'Made-up decision 20: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.7607834, 1795604733.7650363, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.765036, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (27, 'snow-game', 'Made-up decision 21: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.7660995, 1795604733.7701514, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.7701511, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (28, 'snow-game', 'Made-up decision 22: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.7714841, 1795604733.7764347, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.7764342, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (29, 'snow-game', 'Made-up decision 23: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.7798193, 1795604733.7832315, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.7832313, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (30, 'snow-game', 'Made-up decision 24: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.7857869, 1795604733.7891839, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.7891834, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (31, 'snow-game', 'Made-up decision 25: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.7910924, 1795604733.7953224, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.7953222, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (32, 'snow-game', 'Made-up decision 26: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.796749, 1795604733.8014598, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.8014596, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (33, 'snow-game', 'Made-up decision 27: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.8028212, 1795604733.8072815, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.8072808, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (34, 'snow-game', 'Made-up decision 28: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.808707, 1795604733.813189, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.8131888, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (35, 'snow-game', 'Made-up decision 29: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.8143263, 1795604733.8186514, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.818651, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);
INSERT INTO questions (id, project, question, context, options, default_answer, kind, blocking, status, answer, answer_note, standing, answered_via, answered_at, applied_at, asked_by, session_id, task_id, source, created_at, updated_at, blocking_reason, asked_blocking, gate_blocking, taken_by, waited_at, see, see_none, harvested_at, handoff_at, handoff_reason, project_asked, steps, lint, flag, flagged_at, later_until) VALUES (36, 'snow-game', 'Made-up decision 30: ship it?', '', '[{"label": "Yes"}, {"label": "No"}]', 'Yes', 'decision', 0, 'open', NULL, NULL, 0, NULL, NULL, NULL, 'agent', NULL, 1, NULL, 1795604733.8205884, 1795604733.823693, 'risk word "ship" (live)', 0, 1, NULL, NULL, '[]', NULL, 1795604733.8236928, NULL, NULL, 0, '[]', '["no context: say what it is about and what each answer does (-c, 2-4 bullets)"]', NULL, NULL, NULL);

-- Every other table, by hand.
INSERT INTO events (id, machine, agent, session, project, model, ts, kind, text, subagent, speed, calls, input, output, cache_read, cache_write_5m, cache_write_1h, reasoning, tools, cost_reported, effort) VALUES
  ('e1', 'devbox', 'Claude Code', 's1', 'snow-game', 'claude-opus-5-5', 1764012345.1234567, 'usage', NULL, 0, 'fast', 1, 10, 500, 9007199254740993, 0, 0, 0, 3, 0.1 + 0.2, 'high'),
  ('e2', 'devbox', 'Claude Code', 's1', NULL, NULL, 1764012346.0, 'prompt', 'a tab	here, a backslash \ and \N, a newline
and a CR' || char(13) || ' and emoji 🚀 Příliš žluťoučký kůň', 1, NULL, 1, 0, 0, 0, 0, 0, 0, 0, NULL, NULL),
  ('e3 ''quoted''', 'laptop', 'Codex', 's2', '', '', 1e-7, 'usage', '', 0, '', 121, 657890, 128000, 17396992, 664356, 658358, 32000, 178, 1.5e300, '');
INSERT INTO live (machine, seen_at, sessions) VALUES ('devbox', 1764012345.5, '[{"id":"s1","title":"Tabs\tand \"quotes\""}]'), ('laptop', 1764000000, '[]');
INSERT INTO machines (name, last_seen, version, counts) VALUES ('devbox', 1764012345.75, 6, '{"events":3}'), ('laptop', NULL, NULL, NULL);
INSERT INTO palette (project, slot) VALUES ('snow-game', 1), ('habit-app', 7);
INSERT INTO hosts (machine, data, seen_at) VALUES ('devbox', '{"disk":"85%","note":"line\nbreak"}', 1764012300.25);
INSERT INTO jobs_seen (machine, job_id, name, kind, first_seen, last_seen, bad_since, own) VALUES
  ('devbox', 'systemd:harvest', 'harvest', 'timer', 1764000000.5, 1764012345.5, NULL, 1),
  ('devbox', 'launchd:sync', NULL, NULL, NULL, NULL, 1764012000.125, NULL);
INSERT INTO alarms (key, text, first_bad, sent_at) VALUES ('disk:devbox', '🖥️ disk 91% full', 1764012000.5, NULL), ('', NULL, NULL, 0.0);
INSERT INTO job_runs (machine, run, job, agent, session, first_seen, last_seen, model, effort) VALUES
  ('devbox', 'pid:42:1764000000', 'systemd:harvest', 'Claude Code', NULL, 1764000000.0, 1764000100.5, 'claude-haiku', NULL),
  ('devbox', 's9', 'systemd:harvest', NULL, 's9', -1.5, 1764000200.0, NULL, 'low');
INSERT INTO session_names (machine, session, title, source, at) VALUES ('devbox', 's1', 'Snow level ships', 'model', 1764012345.5), ('laptop', 's2', 'Ünïcödé — “quotes”', 'hook', 1764012346.0);
INSERT INTO session_titles (key, title, at) VALUES ('devbox:s1', 'Snow level ships', 1764012345.5);
INSERT INTO names_snapshot (id, at, data) VALUES (1, 1764012345.5, '{"s1":"Snow level ships"}');
INSERT INTO task_sessions (task_id, session_id, linked_at) VALUES (1, 'Claude Code:s1', 1764012345.5), (2, 'Claude Code:s1', 1764012346.5);
INSERT INTO task_comments (task_id, at, author, text) VALUES (1, 1764012347.0625, 'agent', 'Line one
line two	tabbed \ backslash'), (2, 1764012348.0, 'owner', '');
INSERT INTO task_effort (task_id, project, active_min, agent_steps, closed_at) VALUES (5, 'beacon', 12.5, 6, 1764012349.0), (4, NULL, 0.0, 0, 1764012350.0);
INSERT INTO task_guesses (task_id, guess_min, at, actual_min) VALUES (5, 30.0, 1764012000.0, 12.5), (3, 45.25, 1764012001.0, NULL);
INSERT INTO gate_log (ts, received_at, machine, session, cwd, tool, input, category, what, mode, approved, error) VALUES
  (1764012345.5, 1764012346.5, 'devbox', 's1', '/work/snow', 'Bash', '{"command":"rm -rf build\\\\n"}', 'destructive', 'rm', 'auto', NULL, NULL),
  (1764012347.5, 1764012348.5, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 'yes', 'timed out');
INSERT INTO rules (kind, project, summary, why, answer, takes_default, evidence, source, proposal_qid, status, fired, last_fired_at, created_at, updated_at, decided_at) VALUES
  ('answer', 'snow-game', 'Clips: the snowball fight first', NULL, 'The snowball fight', 1, '[1,2]', 'sweep', 1, 'active', 2, 1764012345.5, 1764000000.0, 1764012345.5, 1764000001.0),
  ('answer', NULL, 'Never on Sundays', 'his call', 'No', 0, '[]', 'owner', NULL, 'proposed', 0, NULL, 1764000002.0, 1764000002.0, NULL);
INSERT INTO rule_uses (rule_id, question_id, why, at, undone_at) VALUES (1, 1, 'same clip question', 1764012345.5, NULL), (1, 2, 'again', 1764012346.5, 1764012400.0);
INSERT INTO rule_sweeps (at, days, answers, repeated, risky, covered, model_calls, proposed) VALUES (1764000000.5, 30, 246, 1, 1, 0, 0, '[]');
INSERT INTO card_rules (text, source, qid, active, created_at) VALUES ('Say which machine', 'file', NULL, 1, 1764000000.0), ('No ids up front', 'flag', 3, 0, 1764000001.0);
INSERT INTO card_checks (ts, qid, session_id, asked_by, kind, verdict, called, rules, why, question) VALUES
  (1764012345.5, 1, 's1', 'agent', 'ask', 'refused', 1, '["2"]', 'an id up front', 'Task 12 status?'),
  (1764012346.5, NULL, NULL, NULL, 'ending', 'skipped', 0, '[]', NULL, NULL);
INSERT INTO endings (id, session_id, machine, agent, project, title, state, line, next, cwd, reply, actions, source, created_at, updated_at, cleared_at, acted, question_id) VALUES
  (3, 'Claude Code:s1', 'devbox', 'Claude Code', 'snow-game', 'Snow level ships', 'done', '✅ shipped', NULL, '/work/snow', 'long reply
with lines', '[{"label":"Merge"}]', 'hook', 1764012345.5, 1764012346.5, NULL, NULL, 2),
  (7, 'Codex:s2', NULL, NULL, NULL, NULL, 'needs', '❓ pick one', 'Pick A', NULL, NULL, '[]', 'backstop', 1764012347.5, 1764012348.5, 1764012349.5, 'A', NULL);
INSERT INTO ending_seen (session_id, machine, agent, project, title, line, last) VALUES ('Claude Code:s1', 'devbox', 'Claude Code', 'snow-game', NULL, '✅ shipped', 1764012346.5);
INSERT INTO labs_views (key, title, questions, cards, created, updated, asks, changes) VALUES ('clips', 'Which clips work', '["Which clip?"]', '[{"kind":"chart"}]', 1764000000.0, 1764012345.5, 2, '[]');
INSERT INTO labs_calls (at) VALUES (1764012345.5), (1764012345.5), (1764012399.0);
INSERT INTO talk_usage (at, kind, seconds, cost, device) VALUES (1764012345.5, 'hear', 3.25, 0.0012, 'phone'), (1764012345.5, 'hear', 3.25, 0.0012, 'phone'), (1764012400.0, 'say', 0, 0, NULL);
INSERT INTO rank_days (day, key, value) VALUES ('2026-11-24', 'tokens', 123456789.0), ('2026-11-24', 'cost', 12.345678901234567);
INSERT INTO rank_meta (k, v) VALUES ('version', '3'), ('note', '');
INSERT INTO rank_releases (key, day, kind, what) VALUES ('opus-5-5', '2026-11-20', 'model', 'Opus 5.5'), ('x', '2026-11-21', NULL, NULL);
INSERT INTO notices (id, created_at, level, title, body, link, source, key, topic, repeats, alone, state, push_id, read_at) VALUES
  (2, 1764012345.5, 'urgent', '🖥️ devbox offline', 'body with \N and a tab	end', '/machines', '🖥️ devbox', 'offline:devbox', 'machines', 3, 0, 'pushed', 11, NULL),
  (5, 1764012346.5, 'inbox', '✅ done', '', '', '', '', '', 0, 1, 'kept', NULL, 1764012400.0);
INSERT INTO push_subs (endpoint, p256dh, auth, device, created_at, last_ok, last_error) VALUES ('https://push.example/abc', 'key', 'auth', '', 1764000000.0, NULL, 'gone');
-- pushes is empty (pruned), its AUTOINCREMENT mark stays: an id is never handed out twice.
UPDATE sqlite_sequence SET seq = 41 WHERE name = 'notices';
INSERT INTO sqlite_sequence (name, seq) VALUES ('pushes', 11);
