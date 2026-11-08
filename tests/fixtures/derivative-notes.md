# Notes: the derivative from first principles

*Synthetic teaching material written for the Learning Workbench tests. Public, no third-party content.*

The derivative of a function f at a point x measures how fast f changes there. We start with the
average rate of change between x and a nearby point x + h:

    (f(x + h) − f(x)) / h

This is the slope of the secant line through the two points. The derivative is what this quotient
approaches as h shrinks towards 0:

    f′(x) = lim_{h→0} (f(x + h) − f(x)) / h

We never set h = 0 directly: the quotient would become 0/0. Instead we simplify first, so that the
h in the denominator cancels, and only then let h go to 0.

Example: f(x) = x². Then f(x + h) = (x + h)² = x² + 2xh + h², so

    (f(x + h) − f(x)) / h = (2xh + h²) / h = 2x + h,

which approaches 2x as h → 0. So the derivative of x² is 2x.

A geometric way to see the same result: a square with side x has area x². Growing the side by a
small amount h adds two strips of area x·h each and a tiny corner square of area h². The new area
grows by about 2x·h for small h, so the area changes at rate 2x.

The same method gives the derivative of x³: (x + h)³ = x³ + 3x²h + 3xh² + h³, so the quotient is
3x² + 3xh + h², which approaches 3x².
