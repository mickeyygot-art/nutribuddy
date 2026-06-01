# Delta: Current → Target

**Generated:** 2026-05-31
**Current state:** Greenfield — zero analytics instrumentation in codebase.
**Target:** 7 events, 5 user traits, PostHog destination.

No current-state audit was run (no tracking to audit). Every item below is a net-new addition.

To run an audit first: *"audit tracking"* — though for NutriBuddy it will simply confirm zero existing calls.

---

## Summary

| Action | Count |
|--------|-------|
| Add (new events) | 7 |
| Remove | 0 |
| Rename | 0 |
| Keep | 0 |
| **Total target events** | **7** |

---

## Add (all events are new)

| Event | Category | Priority | Why |
|-------|----------|----------|-----|
| `user.joined` | lifecycle | P0 | Top-of-funnel — no way to measure acquisition without this |
| `goal.set` | lifecycle | P0 | Onboarding completion signal; goal is the primary segmentation dimension |
| `meal.logged` | core_value | P0 | **Primary value action** — the retention and engagement metric |
| `summary.sent` | core_value | P1 | Measures bot reliability and nudge rate for inactive users |
| `dashboard.viewed` | core_value | P2 | Measures self-directed engagement; low-frequency, high-intent signal |
| `user.blocked` | lifecycle | P1 | Product health signal — high block rate = UX problem or misuse |
| `user.unblocked` | lifecycle | P2 | Recovery rate after blocking; low rate = blocked = churned |

---

## Add (user traits — new identify() calls)

| Trait | Type | Update Pattern | Why |
|-------|------|---------------|-----|
| `goal` | string enum | on_change | Primary segmentation dimension for all PostHog analysis |
| `language` | string enum | on_change | Segment Thai vs English users |
| `created_at` | datetime | once | Cohort analysis anchor |
| `last_active_at` | datetime | on_change | Recency — identifies dormant users |
| `meals_logged_count` | integer | on_change (increment) | Usage depth signal; activation threshold detection |

---

## Implementation Order (recommended)

Start with the events that unlock the most insight immediately:

**Phase 1 — Funnel visibility (do this first)**
1. `user.joined` + identify() call with initial traits
2. `goal.set` + update goal trait
3. `meal.logged` with `is_first_meal` flag

These three events alone unlock: acquisition count, onboarding completion rate, activation rate (first meal), and D1/D7/D30 retention via PostHog retention analysis.

**Phase 2 — Engagement & health**
4. `summary.sent` (daily + weekly paths)
5. `dashboard.viewed`
6. `user.blocked` + `user.unblocked`

**Phase 3 — Trait freshness**
7. `last_active_at` update on every webhook message
8. `meals_logged_count` increment on every `meal.logged`

---

## Existing Operational Events (NOT migrated to analytics)

The `event_log` Supabase table records five operational event types. These are kept as-is for operational monitoring. They do NOT need to be migrated to PostHog — the analytics tracking plan covers the same signals with cleaner semantics:

| Operational log | Analytics equivalent |
|----------------|---------------------|
| `daily_summary_sent` | `summary.sent` with `summary_type: daily` |
| `weekly_summary_sent` | `summary.sent` with `summary_type: weekly` |
| `block_triggered` | `user.blocked` |
| `unblock` | `user.unblocked` |
| `dashboard_requested` | `dashboard.viewed` |

The operational log can stay in Supabase for backend diagnostics. PostHog gets the analytics version.

---

## What This Unlocks in PostHog

Once implemented, you can immediately answer:

- **How many users join per week?** → `user.joined` count
- **What percentage set a goal during onboarding?** → `goal.set` where `is_initial_set: true` / `user.joined` funnel
- **What percentage log their first meal?** → `meal.logged` where `is_first_meal: true` / `user.joined` funnel
- **D7 retention?** → PostHog retention analysis, returning action = `meal.logged`
- **Do lose_weight users retain better than no_goal users?** → Retention cohort split by `goal` trait
- **Photo vs text — which drives more meals?** → `meal.logged` breakdown by `source`
- **What's the daily summary nudge rate?** → `summary.sent` where `is_nudge: true` / total `summary.sent`
- **How often do users get blocked?** → `user.blocked` / active users ratio
