# Grace borrowing and repeats

A repeat elsewhere in a score does not change a leading grace group's written
neighbours. The performed traversal is now checked at each borrowing pair. If
the two adjacent written bars remain adjacent on every visit, their existing
source-derived borrowed duration is preserved for each pass. This includes
an intact pair repeated together and grace after an unrelated repeat section.

A jump into the second bar or away from the first still requires further
traversal interpretation and remains blocked. Rest-only grace beats participate
in the same check. No grace is dropped, moved into another bar, or re-attacked.

This removes the blanket rejection affecting Cowboys From Hell's bass measure
12: its repeats elsewhere do not cross that borrowing boundary.
