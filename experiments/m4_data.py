"""A stand-in for the Module 4 dataset until the published one is available.

Four tables shaped like the handout's examples. Emp.salary is built from
Emp.age (salary = 1000 x age plus up to 4,999), so the two are strongly
correlated and AND on both breaks the independence assumption.
"""

import random
from pathlib import Path
from dbms.common.schema import INT, VARCHAR, Column
from dbms.storage.database import Database

CITIES = ('Ottawa', 'Toronto', 'Montreal', 'Calgary', 'Halifax', 'Regina', 'Victoria', 'Winnipeg')
SCHEMAS = {
    'Dept': [('did', INT, None), ('title', VARCHAR, 20), ('budget', INT, None), ('city', VARCHAR, 20)],
    'Emp': [('eid', INT, None), ('name', VARCHAR, 20), ('age', INT, None), ('did', INT, None),
            ('salary', INT, None), ('mgr', INT, None), ('city', VARCHAR, 20), ('pad', VARCHAR, 60)],
    'Proj': [('pid', INT, None), ('did', INT, None), ('lead', INT, None), ('budget', INT, None)],
    'Site': [('sid', INT, None), ('pid', INT, None), ('city', VARCHAR, 20)],
}
SIZES = {'Dept': 50, 'Emp': 4000, 'Proj': 300, 'Site': 600}


def rows(sizes: dict, seed: int) -> dict:
    rng = random.Random(seed)
    depts, emps, projs = sizes['Dept'], sizes['Emp'], sizes['Proj']
    dept = [(d, f'dept{d}', rng.randrange(100, 5000), CITIES[d % len(CITIES)]) for d in range(1, depts + 1)]
    emp = []
    for e in range(1, emps + 1):
        age = rng.randrange(20, 71)
        emp.append((e, f'emp{e}', age, rng.randrange(1, depts + 1), 1000 * age + rng.randrange(5000),
                    rng.randrange(1, emps + 1), rng.choice(CITIES), 'p' * rng.randrange(20, 60)))
    proj = [(p, rng.randrange(1, depts + 1), rng.randrange(1, emps + 1), rng.randrange(10, 1000))
            for p in range(1, projs + 1)]
    site = [(s, rng.randrange(1, projs + 1), rng.choice(CITIES)) for s in range(1, sizes['Site'] + 1)]
    return {'Dept': dept, 'Emp': emp, 'Proj': proj, 'Site': site}


def build(directory: Path, sizes: dict = SIZES, seed: int = 3005) -> Path:
    data = rows(sizes, seed)
    with Database(Path(directory), frames=16, create=True) as db:
        for name, schema in SCHEMAS.items():
            db.create_table(name, tuple(Column(name, c, t, n) for c, t, n in schema))
            table = db.table(name)
            for row in data[name]:
                table.insert(row)
            db.save_counts(table, len(data[name]))
    return Path(directory)
