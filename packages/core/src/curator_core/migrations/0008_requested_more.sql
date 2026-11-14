ALTER TABLE reader_settings ADD COLUMN requested_more_day TEXT;
ALTER TABLE reader_settings ADD COLUMN requested_more_count INTEGER NOT NULL DEFAULT 0;
