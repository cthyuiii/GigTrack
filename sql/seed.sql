-- ============================================================
-- GigTrack — seed data
-- Designed to play nicely with the BEFORE INSERT trigger on bookings:
-- we seed tickets with the FULL available_seats count and let the
-- trigger decrement as we insert the seed bookings.
--
-- Passwords are bcrypt hashes for the literal string "password".
-- Regenerate for any real deployment.
-- ============================================================

INSERT INTO users (username, email, password_hash, home_city) VALUES
  ('macc',      'macc@example.com',  '$2b$12$1nWfTl72I7cXuuntYbZCdevCXVSSYW5MCaC3TOxX.j3YXwV4.sv/y', 'Singapore'),
  ('alex_riff', 'alex@example.com',  '$2b$12$1nWfTl72I7cXuuntYbZCdevCXVSSYW5MCaC3TOxX.j3YXwV4.sv/y', 'Singapore'),
  ('mia_beats', 'mia@example.com',   '$2b$12$1nWfTl72I7cXuuntYbZCdevCXVSSYW5MCaC3TOxX.j3YXwV4.sv/y', 'Kuala Lumpur'),
  ('drew_loud', 'drew@example.com',  '$2b$12$1nWfTl72I7cXuuntYbZCdevCXVSSYW5MCaC3TOxX.j3YXwV4.sv/y', 'Tokyo');

INSERT INTO artists (name, genre, country, image_url) VALUES
  ('The Midnight Lanterns', 'Indie Rock',  'UK',        '/static/img/midnight.jpg'),
  ('Velvet Mango',          'R&B',         'USA',       '/static/img/velvet.jpg'),
  ('Kuro & The Static',     'Post-Punk',   'Japan',     '/static/img/kuro.jpg'),
  ('Nadiah',                'Pop',         'Singapore', '/static/img/nadiah.jpg'),
  ('Solar Drift',           'Synthwave',   'Germany',   '/static/img/solar.jpg'),
  ('Habitat 67',            'Indie Folk',  'Canada',    '/static/img/habitat.jpg');

INSERT INTO venues (name, city, country, capacity, lat, lng) VALUES
  ('Esplanade Concert Hall', 'Singapore',    'Singapore', 1600, 1.290800,  103.855700),
  ('The Star Theatre',       'Singapore',    'Singapore', 5000, 1.306600,  103.788200),
  ('Zepp KL',                'Kuala Lumpur', 'Malaysia',  2500, 3.158400,  101.713800),
  ('Liquidroom',             'Tokyo',        'Japan',     1000, 35.658500, 139.701000),
  ('Brixton Academy',        'London',       'UK',        4900, 51.465800, -0.115100);

INSERT INTO concerts (venue_id, headline_artist_id, title, concert_date, status, base_price) VALUES
  (1, 1, 'The Midnight Lanterns — Asia Tour 2026',   '2026-06-12 20:00:00', 'scheduled', 88.00),
  (2, 4, 'Nadiah: Homecoming Live',                   '2026-06-21 19:30:00', 'scheduled', 65.00),
  (3, 2, 'Velvet Mango — Soft Focus Tour',            '2026-07-04 20:30:00', 'scheduled', 95.00),
  (4, 3, 'Kuro & The Static — Loud Static Mini Tour', '2026-07-18 19:00:00', 'scheduled', 70.00),
  (5, 5, 'Solar Drift — Neon Pulse',                  '2026-08-09 21:00:00', 'scheduled', 110.00),
  (1, 6, 'Habitat 67 in Singapore',                   '2026-05-02 20:00:00', 'completed', 75.00);

INSERT INTO concert_artists (concert_id, artist_id, slot_order, role) VALUES
  (1, 1, 1, 'headliner'),
  (1, 6, 2, 'support'),
  (2, 4, 1, 'headliner'),
  (2, 2, 2, 'support'),
  (3, 2, 1, 'headliner'),
  (4, 3, 1, 'headliner'),
  (4, 1, 2, 'support'),
  (5, 5, 1, 'headliner'),
  (6, 6, 1, 'headliner');

-- Seed available_seats == total_seats so the trigger has room to decrement.
INSERT INTO tickets (concert_id, tier, price, total_seats, available_seats) VALUES
  (1, 'GA',  88.00,   800,  800),
  (1, 'VIP', 188.00,  100,  100),
  (2, 'GA',  65.00,  4000, 4000),
  (2, 'Pit', 120.00,  300,  300),
  (3, 'GA',  95.00,  2200, 2200),
  (3, 'VIP', 195.00,  200,  200),
  (4, 'GA',  70.00,   900,  900),
  (5, 'GA',  110.00, 4500, 4500),
  (5, 'VIP', 220.00,  300,  300),
  (6, 'GA',  75.00,  1500, 1500);

-- Seed bookings — the BEFORE INSERT trigger decrements ticket.available_seats.
INSERT INTO bookings (user_id, ticket_id, quantity, total_price, booked_at) VALUES
  (1, 1, 2, 176.00, '2026-04-10 11:14:00'),
  (2, 1, 1,  88.00, '2026-04-11 09:02:00'),
  (1, 3, 4, 260.00, '2026-04-15 19:40:00'),
  (3, 5, 2, 190.00, '2026-04-20 22:18:00'),
  (4, 8, 1, 110.00, '2026-04-22 15:31:00');

-- Mark the past show's GA tier as fully sold (completed concert).
UPDATE tickets SET available_seats = 0 WHERE ticket_id = 10;

INSERT INTO follows (user_id, artist_id) VALUES
  (1, 1), (1, 4), (1, 6),
  (2, 1), (2, 3),
  (3, 2), (3, 4),
  (4, 3), (4, 5);
