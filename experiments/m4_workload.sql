-- Stand-in workload for Module 4: twenty queries over experiments/m4_data.py.
-- Multi-table queries use JOIN ON so the unoptimized baseline (written order,
-- block nested loop, no pushdown) still finishes; q04 and q11 use commas.

-- q01 single table range
SELECT name, age FROM Emp WHERE age > 60;
-- q02 equality
SELECT eid FROM Emp WHERE did = 7;
-- q03 two predicates on independent columns
SELECT eid FROM Emp WHERE did = 7 AND age > 60;
-- q04 comma join with a filter on each side
SELECT e.name, d.title FROM Emp e, Dept d WHERE e.did = d.did AND e.age > 60 AND d.budget > 1000;
-- q05 join on
SELECT e.name, d.title FROM Emp e JOIN Dept d ON e.did = d.did WHERE d.city = 'Ottawa';
-- q06 grouping
SELECT did, COUNT(*) AS n, AVG(salary) AS avg_sal FROM Emp WHERE age > 25 GROUP BY did HAVING COUNT(*) > 100;
-- q07 three way join
SELECT e.name, p.pid FROM Emp e JOIN Dept d ON e.did = d.did JOIN Proj p ON d.did = p.did WHERE p.budget < 100;
-- q08 four way join chain
SELECT s.sid, e.name FROM Site s JOIN Proj p ON s.pid = p.pid JOIN Dept d ON p.did = d.did JOIN Emp e ON e.eid = p.lead WHERE s.city = 'Halifax';
-- q09 distinct
SELECT DISTINCT city FROM Emp;
-- q10 union of two projections
SELECT city FROM Emp UNION SELECT city FROM Dept;
-- q11 comma join, three tables
SELECT d.title, p.pid FROM Dept d, Proj p, Site s WHERE d.did = p.did AND p.pid = s.pid AND d.budget > 4000;
-- q12 subquery in FROM joined to a table
SELECT d.title, t.n FROM (SELECT did, COUNT(*) AS n FROM Emp GROUP BY did) AS t JOIN Dept d ON t.did = d.did WHERE t.n > 120;
-- q13 order by and limit
SELECT eid, salary FROM Emp WHERE age < 25 ORDER BY salary DESC LIMIT 10;
-- q14 correlated columns: age and salary rise together
SELECT eid FROM Emp WHERE age > 60 AND salary > 60000;
-- q15 correlated predicate feeding a join
SELECT e.name, p.pid FROM Emp e JOIN Proj p ON e.eid = p.lead WHERE e.age > 65 AND e.salary > 65000;
-- q16 group by on a join
SELECT d.city, COUNT(*) AS n FROM Emp e JOIN Dept d ON e.did = d.did GROUP BY d.city;
-- q17 self join through managers
SELECT e.name, m.name FROM Emp e JOIN Emp m ON e.mgr = m.eid WHERE e.age > 68 AND m.age < 22;
-- q18 intersect
SELECT city FROM Site INTERSECT SELECT city FROM Dept;
-- q19 join with a range on the larger side
SELECT p.pid, s.sid FROM Proj p JOIN Site s ON p.pid = s.pid WHERE p.budget > 900;
-- q20 three way join, filter only on the last table
SELECT e.eid FROM Emp e JOIN Proj p ON e.eid = p.lead JOIN Site s ON p.pid = s.pid WHERE s.city = 'Regina';
