-- Product images uploaded with /product image (file option). The bytes live in the database so listing messages
-- can attach them again on every edit and after restarts (Discord attachment links expire; the bytes do not).
-- One image per product (the primary key makes a second row impossible); clearing the image deletes the row.
CREATE TABLE product_images (
  product_id INTEGER PRIMARY KEY REFERENCES products (id),
  guild_id INTEGER NOT NULL,
  filename TEXT NOT NULL CHECK (length(filename) BETWEEN 1 AND 255),
  content_type TEXT NOT NULL CHECK (content_type IN ('image/png', 'image/jpeg', 'image/gif', 'image/webp')),
  size INTEGER NOT NULL CHECK (size BETWEEN 1 AND 8388608),
  sha256 TEXT NOT NULL CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
  data BLOB NOT NULL CHECK (typeof(data) = 'blob' AND length(data) = size),
  uploaded_by INTEGER,
  created_at INTEGER NOT NULL
);
CREATE INDEX idx_product_images_guild ON product_images (guild_id);
