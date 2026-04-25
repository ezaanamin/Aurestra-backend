-- ============================================================
-- Aurestra — Default Categories Seeder (SQLite)
-- Usage:  sqlite3 aurestra.db < categories.sqlite.sql
-- ============================================================

INSERT OR IGNORE INTO categories (name, icon, color, cat_type, is_default) VALUES
    ('Food & Snacks',          'food',         '#FF6B6B', 'spending', 1),
    ('Movies',                 'movie',         '#EC4899', 'spending', 1),
    ('Tea',                    'coffee',        '#F59E0B', 'spending', 1),
    ('Therapy',                'brain',         '#8B5CF6', 'spending', 1),
    ('Uber',                   'car',           '#4ECDC4', 'spending', 1),
    ('Audible Subscription',   'headphones',    '#A78BFA', 'spending', 1),
    ('Google One Subscription','google',        '#3B82F6', 'spending', 1),
    ('Ride / Transport',       'car',           '#4ECDC4', 'spending', 1),
    ('Bills & Utilities',      'receipt',       '#3B82F6', 'spending', 1),
    ('Shopping',               'shopping',      '#A78BFA', 'spending', 1),
    ('Healthcare',             'hospital',      '#10B981', 'spending', 1),
    ('Education',              'school',        '#F59E0B', 'spending', 1),
    ('Groceries',              'cart',          '#10B981', 'spending', 1),
    ('Personal Care',          'sparkles',      '#8B5CF6', 'spending', 1),
    ('Online Services',        'web',           '#3B82F6', 'spending', 1),
    ('Gym & Fitness',          'dumbbell',      '#FF6B6B', 'spending', 1),
    ('Income',                 'cash',          '#10B981', 'income',   1),
    ('Bonus',                  'gift',          '#F59E0B', 'income',   1),
    ('Investment',             'trending-up',   '#3B82F6', 'income',   1),
    ('Uncategorized',          'help-circle',   '#64748B', 'both',     1);
