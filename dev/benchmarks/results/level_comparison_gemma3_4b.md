# Level comparison, real LLM: gemma3:4b (Ollama)

Command: `uv run python dev/benchmarks/level_comparison.py --provider ollama --model gemma3:4b --dump <file>`
Config: 5 levels (daily, weekly, monthly, quarterly, yearly), seed 42, 6 months, Alice + Bob.
Run: 62 LLM calls (6 daily, 40 weekly, 12 monthly, 4 quarterly), 2m24s wall. Same call/summary counts as the mock baseline (62 calls).
Synthetic timeline (week 1 = 2026-01-05): honeymoon wk1-4, settling wk5-8, argument wk9-12 (~Mar), quiet wk13-16 (~Apr), holiday wk17-20 (~May), normal wk21-24 (~Jun).

## Quality assessment

| Check | Result |
|---|---|
| Specific topics, not "discussed things" | Mostly yes. Weekly: surprise dinner, electricity bill, school event, travel bookings, summer trip, sitter/date night. Weak: some weeklies are vague ("juggling work and family"). |
| Emotional arc (honeymoon, settling, argument, resolution) | Preserved at weekly and monthly level: Jan "excitement/plans", Feb "exhaustion/electricity bill", Mar "isolation/resentment, space, apologies, routines", Apr "distance", May "holiday stress/budget", Jun "confidence/gratitude". |
| Key events named | Partly. The argument is never called an "argument" at monthly/quarterly level (Alice Mar: "feelings of isolation and resentment... agreed-upon routines and apologies"; Bob W10 names "bias towards his mother" and "space"). Holiday stress named in W18-W21 and May. Concrete triggers (partner late without calling, chore rota) mostly dropped above weekly. |
| Could a reader get the 6-month story from summaries only? | Mostly. The monthly sequence tells it: happy start, fatigue, conflict and repair, distance, holiday stress, recovery. Specific causes and the resolution mechanics are thin. |
| Higher levels synthesize vs concatenate | Synthesize. Quarterly Q1 combines "achievements... exhaustion, financial stress... isolation and resentment... actively working to address". Q2 gives a trajectory ("initially struggling... ultimately confident"). Not a concatenation. |

Defects:
- Date/phase mismatch in monthly text: the generated data's "holiday" phase lands in May, so May is a holiday summary (a data-generator artifact, not an LLM error).
- Repetition across adjacent weeklies (Alice W06-W08, W14-W17 near-identical).
- Third-person "Alice/Bob" only; each partner is summarised separately, so the joint story must be inferred.
- Minor: "Q1 2026-Q1" awkward phrasing (Bob Q1); daily summaries only 6 (message threshold) and one weekly period missing per partner where items < MIN_ITEMS (Alice W09 etc).
- Some quarterly claims are vaguer than sources ("fluctuating emotions"). No hallucinated events noticed.
- The Q1 summaries for the argument ("isolation and resentment") do mention it but the Bob Q1 text calls it "frustration" and "boundaries".

Verdict: gemma3:4b output is coherent, valid JSON on all 62 calls (0 failures), and preserves the emotional arc and many specific topics. Weakness: abstraction loses concrete event names (argument cause, chore rota) at monthly+ levels. A larger model (qwen3:8b) would likely help with explicit event naming and less repetition; worth a comparison run, but gemma3:4b is adequate for a baseline. Consider adding "name concrete events and causes; avoid generic wording" to `SUMMARY_SYSTEM` regardless of model.

## Representative summaries

### Alice / weekly 2026-W03 (archived)
This week, Alice experienced a mix of positive developments, including a successful surprise dinner, shared responsibilities, and professional recognition, alongside ongoing plans for a weekend trip and their shared future.

### Bob / weekly 2026-W07 (archived)
This week focused on addressing Bob’s need for downtime, his exhaustion from work, and his desire for a more organized household chore schedule.

### Bob / weekly 2026-W10 (archived)
This week has been marked by Bob’s heightened emotional responses, fueled by feelings of neglect, resentment, and a belief that his partner is biased towards his mother, leading to a request for space following a heated exchange.

### Alice / weekly 2026-W12 (archived)
This week focused on establishing clearer communication and boundaries within the relationship, primarily through agreed-upon routines and apologies for past emotional responses.

### Alice / weekly 2026-W16 (archived)
This week, Alice has been juggling her work commitments with her family responsibilities, leading to a noticeable distance and brief communication patterns.

### Bob / weekly 2026-W20 (archived)
This week, Bob’s primary concerns revolved around holiday preparations, particularly the budget for gifts and the stress of hosting both families, with ongoing gratitude for his partner’s assistance.

### Alice / monthly 2026-03 (archived)
March 2026 was primarily dedicated to addressing Alice’s feelings of isolation and resentment within the relationship, focusing on establishing clearer communication boundaries and accountability through agreed-upon routines and apologies.

### Bob / monthly 2026-05 (archived)
Throughout May 2026, Bob consistently voiced anxieties regarding the upcoming holidays, particularly the financial and logistical burdens of hosting both families, while expressing gratitude for his partner's support and taking proactive steps to address these concerns.

### Alice / quarterly 2026-Q1
Alice’s Q1 2026 was characterized by a blend of positive advancements, such as professional achievements and future plans, alongside challenges related to exhaustion, financial stress, and underlying feelings of isolation and resentment within her relationship, which she was actively working to address.

### Alice / quarterly 2026-Q2
Throughout Q2 2026, Alice experienced fluctuating emotions related to managing family and work responsibilities, initially struggling with stress and distance, but ultimately demonstrating increased confidence and proactive engagement with her partner's support.

### Bob / quarterly 2026-Q1
Throughout Q1 2026-Q1, Bob demonstrated fluctuating emotions, ranging from enthusiastic optimism to exhaustion and frustration, navigating professional successes, logistical challenges, and a growing focus on open communication and establishing boundaries.

### Bob / quarterly 2026-Q2
Throughout Q2 2026, Bob navigated anxieties related to finances, family responsibilities, and the upcoming holidays, while actively working with his partner to improve their relationship dynamic and create shared experiences.

## Raw output (all summaries)

### Alice / daily 2026-01-10 (archived)
Alice experienced a productive and exciting day, marked by professional success, shared activities, and future planning with her partner.

### Alice / daily 2026-01-22 (archived)
Alice experienced a productive day at work, expressed excitement about their shared living plans, and began discussing a weekend getaway.

### Alice / daily 2026-02-01 (archived)
The session focused on Alice's desires for shared experiences, including weekend trips and trying new restaurants, and her appreciation for spontaneous romantic gestures.

### Alice / daily 2026-06-21 (archived)
During daily sessions on June 21, 2026, Alice reported positive progress in several key areas, including conflict management, childcare arrangements, and appreciation for her partner's assistance around the home.

### Alice / weekly 2026-W03 (archived)
This week, Alice experienced a mix of positive developments, including a successful surprise dinner, shared responsibilities, and professional recognition, alongside ongoing plans for a weekend trip and their shared future.

### Alice / weekly 2026-W06 (archived)
This week, Alice is juggling a school event, exhaustion from work, and concerns about household responsibilities, particularly the electricity bill.

### Alice / weekly 2026-W07 (archived)
This week, Alice expressed fatigue from her workload and a need for quiet time, alongside concerns about the electricity bill and a forthcoming school event.

### Alice / weekly 2026-W08 (archived)
This week, Alice’s concerns revolved around her workload, the electricity bill, and her need for quiet time, alongside a new school event she needs to attend.

### Alice / weekly 2026-W10 (archived)
This week focused on Alice’s feelings of isolation, resentment, and a perceived imbalance in the relationship, particularly concerning communication and support from her partner and his family.

### Alice / weekly 2026-W11 (archived)
This week, Alice’s feelings of isolation and resentment were prominent, stemming from communication breakdowns, perceived parental bias, and unequal workload distribution, leading to a request for space.

### Alice / weekly 2026-W12 (archived)
This week focused on establishing clearer communication and boundaries within the relationship, primarily through agreed-upon routines and apologies for past emotional responses.

### Alice / weekly 2026-W13 (archived)
This week focused on establishing clear communication and addressing a recent argument, with Alice taking steps to manage her reactions and demonstrate accountability.

### Alice / weekly 2026-W14
This week, Alice’s busy work schedule and short communication style created a slight distance, while she also took on extra responsibility with her children’s homework.

### Alice / weekly 2026-W15 (archived)
This week, Alice juggled her work commitments with her family responsibilities, reporting a feeling of distance while maintaining brief communication channels.

### Alice / weekly 2026-W16 (archived)
This week, Alice has been juggling her work commitments with her family responsibilities, leading to a noticeable distance and brief communication patterns.

### Alice / weekly 2026-W17 (archived)
This week, Alice has been focused on her work and family responsibilities, leading to a noticeable distance in her communication.

### Alice / weekly 2026-W18 (archived)
This week, Alice focused on managing holiday logistics, expressing gratitude for her partner's help and grappling with the stress of coordinating two families.

### Alice / weekly 2026-W19 (archived)
During weekly sessions in 2026-W19, Alice expressed gratitude for her partner's assistance with travel arrangements and focused on creating calm holiday experiences for her children, while also grappling with budget concerns and household workload.

### Alice / weekly 2026-W20 (archived)
This week, Alice primarily focused on holiday preparations, expressing concerns about the budget and wanting to share the burden of cooking, while also appreciating her partner's assistance with travel arrangements.

### Alice / weekly 2026-W21 (archived)
Alice experienced significant stress related to hosting both families and managing holiday preparations, seeking support and expressing her need for shared responsibilities.

### Alice / weekly 2026-W22 (archived)
This week, Alice expressed gratitude for her partner's assistance, felt secure in their communication, and proactively managed logistical details like school pickup and a date night with a sitter, while continuing to plan for a summer vacation.

### Alice / weekly 2026-W23 (archived)
This week saw Alice taking on new responsibilities, expressing gratitude for the couple's communication skills, and actively planning for a future summer trip.

### Alice / weekly 2026-W24 (archived)
This week focused on Alice solidifying positive changes in the relationship dynamic, taking on new responsibilities, and expressing appreciation for her partner's efforts.

### Alice / monthly 2026-01 (archived)
Alice experienced a month of significant progress in her relationship, marked by professional successes, exciting plans for the future, and shared activities with her partner.

### Alice / monthly 2026-02 (archived)
Throughout February 2026, Alice navigated a busy schedule marked by work exhaustion, financial worries regarding the electricity bill, and a need for personal time, alongside a new school event and desires for shared experiences.

### Alice / monthly 2026-03 (archived)
March 2026 was primarily dedicated to addressing Alice’s feelings of isolation and resentment within the relationship, focusing on establishing clearer communication boundaries and accountability through agreed-upon routines and apologies.

### Alice / monthly 2026-04 (archived)
Throughout April 2026, Alice expressed exhaustion and stress related to managing household tasks, family responsibilities, and work, leading to a noticeable distance in her communication.

### Alice / monthly 2026-05 (archived)
Throughout May 2026, Alice focused on managing holiday preparations, expressing gratitude for her partner's support while simultaneously navigating budget anxieties and the complexities of hosting multiple family gatherings.

### Alice / monthly 2026-06 (archived)
Throughout June 2026, Alice demonstrated increased confidence and proactive engagement in the relationship, focusing on positive changes and expressing appreciation for her partner's support.

### Alice / quarterly 2026-Q1
Alice’s Q1 2026 was characterized by a blend of positive advancements, such as professional achievements and future plans, alongside challenges related to exhaustion, financial stress, and underlying feelings of isolation and resentment within her relationship, which she was actively working to address.

### Alice / quarterly 2026-Q2
Throughout Q2 2026, Alice experienced fluctuating emotions related to managing family and work responsibilities, initially struggling with stress and distance, but ultimately demonstrating increased confidence and proactive engagement with her partner's support.

### Bob / daily 2026-02-22 (archived)
Bob’s day was marked by work fatigue, a desire for a more organized home environment, and anxieties about finances and personal time.

### Bob / daily 2026-03-14 (archived)
The day focused on addressing Bob's feelings of resentment and a perceived imbalance in the relationship, alongside his need for space following an argument.

### Bob / weekly 2026-W02 (archived)
This week, Bob expressed excitement about the progression of their relationship, including shared activities, future plans for travel and living together, and a new shared responsibility.

### Bob / weekly 2026-W03 (archived)
During weekly 2026-W03, Bob expressed continued excitement about his relationship, particularly regarding shared activities like dinners, restaurant exploration, and weekend trips, alongside his plans for cohabitation and recent professional achievements.

### Bob / weekly 2026-W04 (archived)
This week focused on shared experiences and logistics, with Bob expressing appreciation for a special gesture, planning a future trip, and adjusting shared responsibilities.

### Bob / weekly 2026-W05 (archived)
This week, Bob experienced professional success and expressed excitement about their upcoming move and shared responsibilities, while also planning a weekend getaway with their partner.

### Bob / weekly 2026-W06 (archived)
This week focused on addressing Bob's exhaustion and establishing routines for home management and personal downtime, alongside logistical planning for a school event.

### Bob / weekly 2026-W07 (archived)
This week focused on addressing Bob’s need for downtime, his exhaustion from work, and his desire for a more organized household chore schedule.

### Bob / weekly 2026-W10 (archived)
This week has been marked by Bob’s heightened emotional responses, fueled by feelings of neglect, resentment, and a belief that his partner is biased towards his mother, leading to a request for space following a heated exchange.

### Bob / weekly 2026-W12 (archived)
This week focused on solidifying communication and agreements between Bob and his partner, primarily through establishing a chore rota and addressing past conflict with apologies and open conversation.

### Bob / weekly 2026-W13 (archived)
This week saw a significant shift in the dynamic as Bob took responsibility for his actions and expressed feeling heard, leading to a calmer and more productive conversation.

### Bob / weekly 2026-W14
This week, Bob has been juggling work commitments and family responsibilities, leading to brief communication and a feeling of distance.

### Bob / weekly 2026-W15 (archived)
This week saw Bob maintaining his responsibilities with the children and working long hours, while also exhibiting a noticeable emotional distance.

### Bob / weekly 2026-W16 (archived)
This week, Bob has been juggling work demands and family responsibilities, leading to a noticeable distance and brief communication patterns.

### Bob / weekly 2026-W17 (archived)
This week, Bob has been primarily focused on work and communicating briefly, while also managing the children's homework and expressing a feeling of distance.

### Bob / weekly 2026-W18 (archived)
This week, Bob expressed enjoyment in decorating the tree while simultaneously grappling with financial anxieties regarding holiday gifts and the demands of household tasks.

### Bob / weekly 2026-W19 (archived)
This week, Bob expressed concerns about hosting both families during the holidays, managing the gift budget, and ensuring the kids have calm holiday experiences, alongside enjoying family time.

### Bob / weekly 2026-W20 (archived)
This week, Bob’s primary concerns revolved around holiday preparations, particularly the budget for gifts and the stress of hosting both families, with ongoing gratitude for his partner’s assistance.

### Bob / weekly 2026-W21 (archived)
Bob's primary concerns this week revolved around managing holiday stress, particularly related to finances and hosting multiple family gatherings, while appreciating his partner's assistance.

### Bob / weekly 2026-W22 (archived)
This week saw Bob taking on a new responsibility, expressing pride in the couple's progress in managing conflict, and appreciating small gestures of support, while also planning a date night with a sitter.

### Bob / weekly 2026-W23 (archived)
This week, Bob expressed gratitude for help with chores and continued to focus on planning a summer trip.

### Bob / weekly 2026-W24 (archived)
This week saw Bob acknowledging positive changes in the couple’s dynamic, including improved conflict management and his partner’s increased contribution to household tasks, alongside a planned date night.

### Bob / weekly 2026-W25 (archived)
This week saw Bob expressing gratitude for small acts of service, continuing his savings goal, and feeling confident in the couple's improved approach to disagreements.

### Bob / monthly 2026-01 (archived)
Bob consistently expressed enthusiasm for their relationship throughout January 2026, focusing on shared activities, future plans, and adjusting their shared commitments.

### Bob / monthly 2026-02 (archived)
February 2026 was primarily focused on managing Bob’s exhaustion, establishing household routines, and planning for a school event, while ongoing concerns about finances and work continued to surface.

### Bob / monthly 2026-03 (archived)
March 2026 was a period of focused communication and accountability for Bob, marked by a shift in his behavior and the establishment of practical solutions to address his feelings of resentment and the need for space.

### Bob / monthly 2026-04 (archived)
Throughout April 2026, Bob has been juggling work, family responsibilities, and expressed concerns about finances and hosting, while also displaying a growing emotional distance.

### Bob / monthly 2026-05 (archived)
Throughout May 2026, Bob consistently voiced anxieties regarding the upcoming holidays, particularly the financial and logistical burdens of hosting both families, while expressing gratitude for his partner's support and taking proactive steps to address these concerns.

### Bob / monthly 2026-06 (archived)
During June 2026, Bob continued to express gratitude for support and actively worked on improving the relationship dynamic, including planning a summer trip and a date night.

### Bob / quarterly 2026-Q1
Throughout Q1 2026-Q1, Bob demonstrated fluctuating emotions, ranging from enthusiastic optimism to exhaustion and frustration, navigating professional successes, logistical challenges, and a growing focus on open communication and establishing boundaries.

### Bob / quarterly 2026-Q2
Throughout Q2 2026, Bob navigated anxieties related to finances, family responsibilities, and the upcoming holidays, while actively working with his partner to improve their relationship dynamic and create shared experiences.
