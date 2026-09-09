# Dashboard presentation review

The six recommendations below are implemented. This record preserves the
original findings and describes the resulting behavior. Review and development
used the verified fictional demo and approved public screenshots; no personal
dashboard was inspected. The dark theme, compact tables, and detailed financial
views remain the foundation.

## Implemented recommendations

### 1. Correct the Holdings heading when switching layouts

The original review reproduced a Table account subtotal remaining above Board
rows with a different scope. The shared heading now follows the active layout
and its filters. Empty selections show zero; historical-date and missing-data
Board notices clear the amount. Returning to Table restores its own filtered
snapshot subtotal.

Table and Board retain independent filters, with visible explanations. In
Board's **By Symbol** mode, an account selection finds symbols held there;
quantities, values, and P&L remain combined across all accounts holding those
symbols. **By Account** supplies account-specific figures. This preserves the
existing financial aggregation while making its scope explicit.

[Holdings](../src/dashboard/app/10-holdings.js) and
[Board](../src/dashboard/app/15-board.js) share the heading update. The
[Holdings presentation regressions](../tests/test_holdings_presentation.py)
exercise actual rendered filter handlers, layout/group changes, independent
selections, empty results, missing data, and historical snapshots with a
fictional symbol held in two accounts.

### 2. Make Performance follow the selected account and window

Previously, fixed lifetime cards, windowed cards, and benchmark cards competed
for attention, with repeated figures at Total/Lifetime. Account and date
controls now precede one primary selected-scope summary: Portfolio Value,
dollar Total Return, cumulative TWR, and annualized TWR. Dollar return and TWR
remain distinct measures.

A compact **Whole portfolio · all-time reference** retains the fixed figures.
Realized/unrealized gains, contributions, benchmark figures, and return-method
comparisons remain available in disclosures. The benchmark chart follows the
primary summary; Sharpe, Sortino, and selected-scope Max Balance Drawdown live
under **Risk**. Disclosure state survives filter changes, and controls retain
focus across their rerender.

Dollar figures, TWR observations, and chart dates have separate labels where
their measurement boundaries differ. Unrealized remains an end-date level.
Annual returns use all available years for the selected account; winners and
losers retain their latest-position, lifetime, whole-portfolio scope. These
contracts are covered by the [Performance presentation tests](../tests/test_performance_presentation.py)
and existing Python/JavaScript return checks.

### 3. Use color and type to emphasize meaning

Holdings previously colored ordinary positive quantities, prices, values, and
cost basis like gains. Those levels now use neutral text; gains, losses, and
returns keep sign colors. Account and sector colors still identify categories.
Portfolio value is larger and more prominent in the top summary, with return
and recent change as supporting figures. Card spacing and alignment are more
consistent while tabular numerals and keyboard focus treatments remain.

The [Holdings presentation regressions](../tests/test_holdings_presentation.py)
check rendered neutral levels alongside positive and negative gains.

### 4. Put scope and date beside the figures they describe

Overview/Holdings snapshot sections now show **Latest** or **As of** labels
using the actual available snapshot date. The top summary, target allocation,
and concentration are labeled latest; Lot Method Comparison is lifetime.
History shows its own observation range. Native metric-guide disclosures make
definitions available by keyboard and touch as well as hover.

The original Tax review overstated a selected-year/current-year mismatch.
Tax Rates & Income already follows the selected year; **All Years** uses the
latest dataset year for rate assumptions. The implemented labels distinguish
those assumptions from latest open-lot values and eligibility. Estimated
harvest savings use the selected rates. Potential Wash Sales and the Form 8949
CSV include all recorded years regardless of the realization filter. The
[Tax presentation tests](../tests/test_tax_presentation.py) protect these
separate scopes.

Holdings now distinguishes its grouped summary from **Positions & lots**, and
the **P&L board** toggle describes the alternate view's purpose.

### 5. Reduce the effort of using the dashboard on a phone

Navigation already scrolled horizontally before this batch. The implementation
retains that behavior and adds measured horizontal-overflow cues to wide tables.
The first identifying column stays visible in Holdings and Board; Positions
puts the symbol first. Expanded lot detail and nested lot-table cells remain
outside the sticky-column rule. The same overflow measurement maintains
keyboard access and clears cues when content fits.

Touch controls have larger targets. Performance uses a compact account selector,
Lifetime/1y/YTD/3mo shortcuts, and a More ranges menu on phones; desktop keeps
its full controls. The public demo introduction is shorter, with its fictional
designation always visible and project details under **About this demo**. The
banner change affects the public demo, not generated personal dashboards.

### 6. Make common destinations and chart choices easier to find

Performance now follows Overview and Holdings in navigation. Tab IDs and deep
links are preserved, and keyboard travel follows the visible order.

History opens with **Balance & contributions** over the lifetime range, showing
portfolio value and net contributed separately. **Account mix** remains one
click away. Lifetime, 1y, and YTD buttons and More ranges sit beside the chart;
Chart Options retains custom dates, detailed series, overlays, benchmarks, and
composition settings. Choosing Balance & contributions restores that simple
series selection without changing the chosen date range.

The first switch from a preset to Custom starts with that visible observation
range; returning to Custom preserves previously entered dates. The
[History presentation tests](../tests/test_history_presentation.py) cover these
transitions and the distinct balance/contribution series. Quick and advanced
chart controls retain keyboard focus after rerendering.

## Evidence and acceptance

Before this batch, the Performance benchmark heading appeared roughly 760 pixels
down the desktop page and 1,420 pixels down the phone page. These measurements
include the public demo banner and are observations of the fictional sample,
not targets for every dataset. The initial review's twenty tab/viewport states
at 1440 and 390 pixels showed no page-wide overflow or script errors. These are
historical observations, not measurements of the updated layout or user testing.

Behavioral checks cover filter and layout totals, selected and fixed scopes,
date boundaries, disclosure state, native controls, keyboard navigation, lot
expansion, and narrow-table identity. The assembled fictional demo is the browser
validation target. Final validation results belong in
[Engineering review](REVIEW.md#validation-and-limits).

Follow the [development loop](../CONTRIBUTING.md#development-loop) and
[screenshot publication rules](PRIVACY.md#before-publishing-a-demo-or-screenshot)
for subsequent changes. Financial aggregation, return engines, and account/date
contracts remain shared; presentation changes must not silently redefine them.
