-- ============================================================
-- Aurestra — Default Categories Seeder (PostgreSQL)
-- Usage:  psql -U <user> -d <dbname> -f categories.postgres.sql
-- ============================================================

INSERT INTO categories (name, icon, color, cat_type, is_default) VALUES
    ('Food & Snacks',          'food',         '#FF6B6B', 'spending', TRUE),
    ('Movies',                 'movie',         '#EC4899', 'spending', TRUE),
    ('Tea',                    'coffee',        '#F59E0B', 'spending', TRUE),
    ('Therapy',                'brain',         '#8B5CF6', 'spending', TRUE),
    ('Uber',                   'car',           '#4ECDC4', 'spending', TRUE),
    ('Audible Subscription',   'headphones',    '#A78BFA', 'spending', TRUE),
    ('Google One Subscription','google',        '#3B82F6', 'spending', TRUE),
    ('Ride / Transport',       'car',           '#4ECDC4', 'spending', TRUE),
    ('Bills & Utilities',      'receipt',       '#3B82F6', 'spending', TRUE),
    ('Shopping',               'shopping',      '#A78BFA', 'spending', TRUE),
    ('Healthcare',             'hospital',      '#10B981', 'spending', TRUE),
    ('Education',              'school',        '#F59E0B', 'spending', TRUE),
    ('Groceries',              'cart',          '#10B981', 'spending', TRUE),
    ('Personal Care',          'sparkles',      '#8B5CF6', 'spending', TRUE),
    ('Online Services',        'web',           '#3B82F6', 'spending', TRUE),
    ('Gym & Fitness',          'dumbbell',      '#FF6B6B', 'spending', TRUE),
    ('Income',                 'cash',          '#10B981', 'income',   TRUE),
    ('Bonus',                  'gift',          '#F59E0B', 'income',   TRUE),
    ('Investment',             'trending-up',   '#3B82F6', 'income',   TRUE),
    ('Uncategorized',          'help-circle',   '#64748B', 'both',     TRUE)
ON CONFLICT (name) DO UPDATE
    SET icon       = EXCLUDED.icon,
        color      = EXCLUDED.color,
        cat_type   = EXCLUDED.cat_type,
        is_default = EXCLUDED.is_default;
