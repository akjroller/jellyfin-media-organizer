# Community beta guide

JMO is looking for beta testers with difficult TV-show libraries. It is not a
finished universal media manager and currently focuses on TV shows with TVMaze
as the configured provider.

## What to test

1. Start with `jmo demo` and confirm the synthetic workflow makes sense.
2. Run `jmo doctor` against a copy or a small disposable test library.
3. Run `jmo plan` and `jmo review --summary`; do not start with `jmo apply`.
4. Try `jmo report` and confirm the generated bundle contains no private paths.
5. If you choose to test apply, use a disposable synthetic library first and
   follow the exact approval and journal requirements.

## What to report

Useful reports include:

- JMO version, operating system, and Python version;
- the command and exit code;
- counts from `summary.txt` or `jmo inspect`;
- plan, decision, and review-session hashes;
- a minimal fabricated filename example;
- whether the run was online or offline.

Never attach real inventories, raw private filenames, provider caches,
credentials, approval tokens, or unredacted journals. Use `jmo report` for a
shareable diagnostic bundle.

## Community prompts

- What filename pattern did JMO fail to understand?
- What organizer are you replacing, and what did it get wrong?
- Which review explanation was unclear?
- What would make you trust the apply boundary?
- Which platform and shell should the quickstart support better?

Use the issue templates for reproducible defects and feature proposals. Use
GitHub Discussions for questions, workflow feedback, and filename examples.
