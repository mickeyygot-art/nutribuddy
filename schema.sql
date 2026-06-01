-- NutriBuddy Database Schema
-- Run this in Supabase SQL Editor (supabase.com → your project → SQL Editor)

CREATE TABLE IF NOT EXISTS users (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  line_user_id TEXT UNIQUE NOT NULL,
  goal TEXT DEFAULT 'no_goal',        -- lose_weight | eat_clean | build_muscle | no_goal
  language TEXT DEFAULT 'th',         -- th | en
  created_at TIMESTAMPTZ DEFAULT NOW(),
  last_active_at TIMESTAMPTZ,         -- YOL-35: updated on every message
  last_suggestion TEXT,              -- YOL-43: last coaching move sent (for follow-through)
  last_suggestion_at TIMESTAMPTZ,    -- YOL-43: when that suggestion was sent
  last_winback_at TIMESTAMPTZ,       -- YOL-51: when the last win-back nudge was sent
  coaching_profile TEXT,             -- YOL-59: compact learned profile (food/behavior, no PII)
  profile_updated_at TIMESTAMPTZ,    -- YOL-59: when the profile was last refreshed
  checkin_pending_at TIMESTAMPTZ     -- YOL-60: when an outcome check-in was last asked (awaiting reply)
);

-- YOL-60: Outcome check-ins — self-reported wellbeing over time (no PII; weight only if volunteered)
CREATE TABLE IF NOT EXISTS checkins (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID REFERENCES users(id) ON DELETE CASCADE,
  energy INTEGER,                    -- self-rated 1-5, nullable
  goal_progress TEXT,                -- 'better' | 'same' | 'worse' | null
  weight NUMERIC,                    -- kg, ONLY if the user volunteered it
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS meals (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID REFERENCES users(id) ON DELETE CASCADE,
  description TEXT,
  meal_type TEXT,                     -- breakfast | lunch | dinner | snack | late_snack
  source TEXT DEFAULT 'photo',        -- YOL-35: 'photo' | 'text'
  logged_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS off_topic_log (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID REFERENCES users(id) ON DELETE CASCADE UNIQUE,
  count INTEGER DEFAULT 0,
  blocked_until TIMESTAMPTZ,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- YOL-35: Event log for engagement metrics
CREATE TABLE IF NOT EXISTS event_log (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID REFERENCES users(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,           -- 'daily_summary_sent' | 'weekly_summary_sent' | 'block_triggered' | 'dashboard_requested' | 'unblock'
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- YOL-35: Migration for existing Supabase projects (run separately if tables already exist)
-- ALTER TABLE users ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMPTZ;
-- ALTER TABLE meals ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'photo';
-- CREATE TABLE IF NOT EXISTS event_log ( ... ); -- see above

-- YOL-43: Coaching follow-through columns
-- ALTER TABLE users ADD COLUMN IF NOT EXISTS last_suggestion TEXT;
-- ALTER TABLE users ADD COLUMN IF NOT EXISTS last_suggestion_at TIMESTAMPTZ;

-- YOL-51: Win-back nudge column
-- ALTER TABLE users ADD COLUMN IF NOT EXISTS last_winback_at TIMESTAMPTZ;

-- YOL-59: Persistent coaching profile columns
-- ALTER TABLE users ADD COLUMN IF NOT EXISTS coaching_profile TEXT;
-- ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_updated_at TIMESTAMPTZ;

-- YOL-60: Outcome check-in column + table
-- ALTER TABLE users ADD COLUMN IF NOT EXISTS checkin_pending_at TIMESTAMPTZ;
-- CREATE TABLE IF NOT EXISTS checkins ( ... );  -- see above
