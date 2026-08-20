# Milan Horáček

Technical Customer Success Manager at Wrike, in Prague. I work on the enterprise
side of platform adoption: API integrations, workflow architecture, and turning
technical configuration into something an executive sponsor can act on.

I write code because a lot of customer success work is repeated judgement over
structured data — who is overallocated, which objectives nobody agreed a metric
for, which account is quietly heading for a bad renewal. Those are rules, and
rules can be written down, tested, and re-run. What can't be automated is the
conversation, which is where the time saved should go.

## What's here

**[technical-csm-projects](https://github.com/mhoracek13-arch/technical-csm-projects)**
— two tools and three smaller demos.

- **Capacity Guardian** reads live Wrike task data, finds overallocated
  teammates, and proposes reassignments that don't just move the overload onto
  someone else. Runs offline against bundled fixtures if you want to try it
  without credentials.
- **Success Plan generator** turns an account brief into an executive Success
  Plan: renewal risk scored factor by factor, objectives with no metric flagged,
  missing stakeholder roles named, milestones scheduled backwards from the
  renewal date.

Both are rules-based and say so. Neither is a model, and the READMEs are
explicit about what they don't do — a churn score you can't interrogate is a
churn score people ignore.

## How I work

Every tool in there separates the analysis from the I/O, so the logic is
testable with plain dictionaries and nothing touches the network in a test.
Several tests exist because I shipped the bug first: the reassignment planner
once handed one person unlimited work without ever recomputing their load, and
there's now a test named after exactly that. A few of the tests were themselves
rewritten after I checked whether they'd catch a break, and found one of them
was quietly asserting its own input.

Bilingual French/English, working Czech. Previously logistics and supply chain
operations, which is where I learned that the constraint is usually people and
process rather than the software.

📍 Prague · [LinkedIn](https://linkedin.com/in/milan-horacek)
