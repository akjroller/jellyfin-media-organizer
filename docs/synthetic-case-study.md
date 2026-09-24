# Synthetic case study: a messy Shows folder

This case study is entirely fabricated. It demonstrates the kind of evidence
JMO is designed to expose before an apply operation. It contains no real paths,
provider caches, credentials, or private filenames.

## Input

```text
Shows/
  Example Show/
    Example.Show.S01E01.1080p.WEB-DL.mkv
    Example.Show.S01E01.720p.WEB-DL.mkv
    Example.Show.S01E02.mkv
    Example.Show.S01E02.en.srt
    Example.Show.Sample.mkv
    poster.jpg
  Mystery Collection/
    Mystery Collection 01.mkv
```

The synthetic provider catalog contains one unambiguous `Example Show` catalog,
two releases of episode 1, one subtitle companion, one sample, and one title
that cannot be safely resolved.

## Plan summary

```text
records=6
matched=2
extra=1
duplicate=1
held=0
suspicious=0
unresolved=1
companions=2
companion_associated=1
companion_ignored=1
preflight_ready=false
preflight_findings=1
```

What that means:

- The higher-quality episode 1 release is the proposed winner.
- The other episode 1 release is a duplicate loser and stays untouched.
- The subtitle is associated only with the proposed winner.
- The sample is classified as an extra and is not treated as an episode.
- The poster is an ignored companion and stays in place.
- `Mystery Collection 01.mkv` is unresolved, so the whole plan remains blocked.

## Review decision

Review records the decision and its evidence; it does not move anything.

```text
duplicate group: Example Show / S01E01
winner: 1080p WEB-DL
loser: 720p WEB-DL
reason: higher verified release quality; destination collision remains protected

held source: Mystery Collection 01.mkv
decision: leave untouched
reason: no unique provider title and coordinate evidence
```

After the unresolved file is explicitly held, the reviewed plan can become
apply-ready. The duplicate loser, ignored poster, and held source still have
`destination=null` and remain untouched.

## Check-only apply

```text
check_only=true
groups_total=2
groups_completed=0
members_moved=0
members_recovered=0
preflight_ready=true
```

That is the complete safety boundary: people can inspect the plan, review
ambiguous evidence, and run check-only validation without changing their media.
See [the apply safety contract](apply-safety-contract.md) for the final gated
mutation path.
