-- NutriBuddy Database Schema
-- Run this in Supabase SQL Editor (supabase.com → your project → SQL Editor)

CREATE TABLE IF NOT EXISTS users (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  line_user_id TEXT UNIQUE NOT NULL,
  goal TEXT DEFAULT 'no_goal',   -- lose_weight | eat_clean | build_muscle | no_goal
  language TEXT DEFAULT 'th',    -- th | en
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS meals (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID REFERENCES users(id) ON DELETE CASCADE,
  description TEXT,
  meal_type TEXT,                -- breakfast | lunch | dinner | snack | late_snack
  logged_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS off_topic_log (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID REFERENCES users(id) ON DELETE CASCADE UNIQUE,
  count INTEGER DEFAULT 0,
  blocked_until TIMESTAMPTZ,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
