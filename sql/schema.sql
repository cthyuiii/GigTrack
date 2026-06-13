-- ============================================================
-- GigTrack - MySQL schema
-- Drop in order respecting FK dependencies
-- ============================================================
SET NAMES utf8mb4;
DROP TRIGGER IF EXISTS trg_booking_decrement_seats;
DROP TRIGGER IF EXISTS trg_booking_restore_seats_on_cancel;
DROP TRIGGER IF EXISTS trg_ticket_vip_price_ins;
DROP TRIGGER IF EXISTS trg_ticket_vip_price_upd;
DROP TABLE IF EXISTS bookings;
DROP TABLE IF EXISTS tickets;
DROP TABLE IF EXISTS concert_artists;
DROP TABLE IF EXISTS follows;
DROP TABLE IF EXISTS concerts;
DROP TABLE IF EXISTS venues;
DROP TABLE IF EXISTS artists;
DROP TABLE IF EXISTS users;

-- ------------------------------------------------------------
CREATE TABLE users (
  user_id       INT AUTO_INCREMENT PRIMARY KEY,
  username      VARCHAR(40)  NOT NULL UNIQUE,
  email         VARCHAR(120) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  home_city     VARCHAR(80),
  is_admin      TINYINT(1) NOT NULL DEFAULT 0,   -- admin dashboard access
  is_active     TINYINT(1) NOT NULL DEFAULT 1,   -- 0 = disabled, can't log in
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_users_city (home_city)
) ENGINE=InnoDB;

-- ------------------------------------------------------------
CREATE TABLE artists (
  artist_id  INT AUTO_INCREMENT PRIMARY KEY,
  name       VARCHAR(120) NOT NULL,
  genre      VARCHAR(60),
  country    VARCHAR(60),
  image_url  VARCHAR(255),
  INDEX idx_artists_genre (genre),
  INDEX idx_artists_name  (name)
) ENGINE=InnoDB;

-- ------------------------------------------------------------
CREATE TABLE venues (
  venue_id  INT AUTO_INCREMENT PRIMARY KEY,
  name      VARCHAR(120) NOT NULL,
  city      VARCHAR(80)  NOT NULL,
  country   VARCHAR(60)  NOT NULL,
  capacity  INT,
  lat       DECIMAL(9,6),
  lng       DECIMAL(9,6),
  INDEX idx_venues_city (city)
) ENGINE=InnoDB;

-- ------------------------------------------------------------
CREATE TABLE concerts (
  concert_id          INT AUTO_INCREMENT PRIMARY KEY,
  venue_id            INT NOT NULL,
  headline_artist_id  INT NOT NULL,
  title               VARCHAR(160) NOT NULL,
  concert_date        DATETIME NOT NULL,
  status              ENUM('scheduled','sold_out','cancelled','completed') NOT NULL DEFAULT 'scheduled',
  base_price          DECIMAL(8,2) NOT NULL,
  view_count          INT NOT NULL DEFAULT 0,
  FOREIGN KEY (venue_id)           REFERENCES venues(venue_id),
  FOREIGN KEY (headline_artist_id) REFERENCES artists(artist_id),
  INDEX idx_concerts_date   (concert_date),
  INDEX idx_concerts_venue  (venue_id),
  INDEX idx_concerts_artist (headline_artist_id),
  -- The home/trending queries always filter on status = 'scheduled';
  -- a composite (status, concert_date) index serves both the filter and
  -- the ORDER BY concert_date in a single index scan.
  INDEX idx_concerts_status_date (status, concert_date)
) ENGINE=InnoDB;

-- M:N junction - supporting artists / festival lineups
CREATE TABLE concert_artists (
  concert_id  INT NOT NULL,
  artist_id   INT NOT NULL,
  slot_order  INT NOT NULL DEFAULT 1,   -- 1 = headliner, 2..N = support
  role        VARCHAR(40) DEFAULT 'support',
  PRIMARY KEY (concert_id, artist_id),
  FOREIGN KEY (concert_id) REFERENCES concerts(concert_id) ON DELETE CASCADE,
  FOREIGN KEY (artist_id)  REFERENCES artists(artist_id)
) ENGINE=InnoDB;

-- ------------------------------------------------------------
CREATE TABLE tickets (
  ticket_id        INT AUTO_INCREMENT PRIMARY KEY,
  concert_id       INT NOT NULL,
  tier             VARCHAR(40) NOT NULL,   -- e.g. GA, VIP, Pit
  price            DECIMAL(8,2) NOT NULL,
  total_seats      INT NOT NULL,
  available_seats  INT NOT NULL,
  FOREIGN KEY (concert_id) REFERENCES concerts(concert_id) ON DELETE CASCADE,
  CONSTRAINT chk_seats_nonneg CHECK (available_seats >= 0),
  CONSTRAINT chk_seats_le_total CHECK (available_seats <= total_seats),
  INDEX idx_tickets_concert (concert_id)
) ENGINE=InnoDB;

-- ------------------------------------------------------------
CREATE TABLE bookings (
  booking_id   INT AUTO_INCREMENT PRIMARY KEY,
  user_id      INT NOT NULL,
  ticket_id    INT NOT NULL,
  quantity     INT NOT NULL,
  total_price  DECIMAL(10,2) NOT NULL,
  status       ENUM('confirmed','cancelled','refunded') NOT NULL DEFAULT 'confirmed',
  booked_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id)   REFERENCES users(user_id),
  FOREIGN KEY (ticket_id) REFERENCES tickets(ticket_id),
  INDEX idx_bookings_user (user_id),
  INDEX idx_bookings_ticket (ticket_id)
) ENGINE=InnoDB;

-- ------------------------------------------------------------
CREATE TABLE follows (
  user_id      INT NOT NULL,
  artist_id    INT NOT NULL,
  followed_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id, artist_id),
  FOREIGN KEY (user_id)   REFERENCES users(user_id)   ON DELETE CASCADE,
  FOREIGN KEY (artist_id) REFERENCES artists(artist_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ============================================================
-- TRIGGERS
-- 1. On booking insert (status=confirmed), decrement seat inventory.
--    If insufficient seats, signal an error so the transaction rolls back.
-- 2. On booking cancellation (UPDATE status -> 'cancelled'), restore seats.
-- ============================================================
DELIMITER //

CREATE TRIGGER trg_booking_decrement_seats
BEFORE INSERT ON bookings
FOR EACH ROW
BEGIN
  DECLARE avail INT;
  SELECT available_seats INTO avail FROM tickets WHERE ticket_id = NEW.ticket_id FOR UPDATE;
  IF avail < NEW.quantity THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'Not enough seats available for this ticket tier';
  END IF;
  UPDATE tickets
     SET available_seats = available_seats - NEW.quantity
   WHERE ticket_id = NEW.ticket_id;
END//

CREATE TRIGGER trg_booking_restore_seats_on_cancel
AFTER UPDATE ON bookings
FOR EACH ROW
BEGIN
  -- Both cancellations AND refunds release the seats back to inventory;
  -- only the transition out of 'confirmed' restores (so flipping a booking
  -- between cancelled and refunded can never double-restore).
  IF NEW.status IN ('cancelled', 'refunded') AND OLD.status = 'confirmed' THEN
    UPDATE tickets
       SET available_seats = available_seats + OLD.quantity
     WHERE ticket_id = OLD.ticket_id;
  END IF;
END//

-- ------------------------------------------------------------
-- 3. Pricing rule: a VIP tier must never be cheaper than a non-VIP tier of
--    the SAME concert. Enforced on both insert and update of tickets.
-- ------------------------------------------------------------
CREATE TRIGGER trg_ticket_vip_price_ins
BEFORE INSERT ON tickets
FOR EACH ROW
BEGIN
  DECLARE bad INT DEFAULT 0;
  IF NEW.tier = 'VIP' THEN
    SELECT COUNT(*) INTO bad FROM tickets
     WHERE concert_id = NEW.concert_id AND tier <> 'VIP' AND price > NEW.price;
  ELSE
    SELECT COUNT(*) INTO bad FROM tickets
     WHERE concert_id = NEW.concert_id AND tier = 'VIP' AND price < NEW.price;
  END IF;
  IF bad > 0 THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'VIP tickets cannot be priced lower than other tiers';
  END IF;
END//

CREATE TRIGGER trg_ticket_vip_price_upd
BEFORE UPDATE ON tickets
FOR EACH ROW
BEGIN
  DECLARE bad INT DEFAULT 0;
  IF NEW.tier = 'VIP' THEN
    SELECT COUNT(*) INTO bad FROM tickets
     WHERE concert_id = NEW.concert_id AND ticket_id <> NEW.ticket_id
       AND tier <> 'VIP' AND price > NEW.price;
  ELSE
    SELECT COUNT(*) INTO bad FROM tickets
     WHERE concert_id = NEW.concert_id AND ticket_id <> NEW.ticket_id
       AND tier = 'VIP' AND price < NEW.price;
  END IF;
  IF bad > 0 THEN
    SIGNAL SQLSTATE '45000'
      SET MESSAGE_TEXT = 'VIP tickets cannot be priced lower than other tiers';
  END IF;
END//

DELIMITER ;
