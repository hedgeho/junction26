-- Validated media references (JSON list, see curator_core.media). Bytes stay on platform CDNs.
ALTER TABLE posts ADD COLUMN media TEXT NOT NULL DEFAULT '[]';
ALTER TABLE social_posts ADD COLUMN media TEXT NOT NULL DEFAULT '[]';
-- Optional author display metadata supplied by source adapters.
ALTER TABLE social_posts ADD COLUMN author_handle TEXT;
ALTER TABLE social_posts ADD COLUMN author_avatar TEXT;
