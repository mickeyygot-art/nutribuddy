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
  last_winback_at TIMESTAMPTZ        -- YOL-51: when the last win-back nudge was sent
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
