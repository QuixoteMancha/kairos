# fairprice/
#
# Fair-price models for Kairos (Kalshi-only prediction market arb engine).
#
# Each model estimates the model's TRUE probability that outcome "A" of a
# binary Kalshi market occurs, independent of the current market price. That
# estimate is later compared against `current_a_price` / `current_b_price`
# (both in [0, 1], where a + b ~= 1 minus the exchange spread) to detect
# mispricing/edge.
#
# Hard rule (real money, fail-closed): if a model lacks the data it needs to
# produce a genuine independent estimate, it MUST fall back to
# fair_a_price = current_a_price (i.e. report zero edge). Never fabricate an
# edge out of missing data - that is a capital-risk landmine.
