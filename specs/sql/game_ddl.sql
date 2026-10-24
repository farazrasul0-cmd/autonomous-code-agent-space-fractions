-- Space Fractions Database Schema Specification
-- Sourced directly from Architecture_Documentation.md Section L

CREATE TABLE games (
  id SERIAL PRIMARY KEY,
  game_state JSONB NOT NULL
);
