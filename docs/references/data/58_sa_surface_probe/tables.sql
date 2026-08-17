.headers on
.mode column
SELECT name FROM sqlite_master WHERE type='table' AND (
  name LIKE '%criterion%' OR name LIKE '%target%' OR name LIKE '%bidweight%' OR name LIKE '%hourly%' OR name LIKE '%pattern%'
);
