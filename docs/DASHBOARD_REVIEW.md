# Dashboard presentation review

The fixes and improvements below are not yet implemented. The review used the
verified fictional demo, the four approved public screenshots, and all ten tabs
at desktop and phone widths. No personal dashboard was inspected. The existing
dark theme, compact tables, and detailed financial views provide a useful base.

## Recommended order

### 1. Correct the Holdings heading when switching layouts

Filtering Table to one account and then switching to Board leaves the shared
heading showing the Table subtotal while Board can show all accounts. This was
reproduced through the actual fictional demo controls. The Table renderer owns
the heading total, but the layout switch does not update or hide it. Make that
heading describe the active view, or keep the Table subtotal inside its layout.

There is a related scope ambiguity: Board's By Symbol account selector keeps
symbols held in the selected account, but their values and P&L remain combined
across all accounts. Cross-account symbol aggregation is intentional; the selector
does not currently explain that meaning. Label it explicitly, or make account
scope a deliberate behavior change with matching financial tests. Table and Board
also maintain independent filters, so changing layouts can change the visible
scope without changing the common heading.

Start in [Holdings](../src/dashboard/app/10-holdings.js), which writes
`byAssetTotalValue`, and [Board](../src/dashboard/app/15-board.js), especially
`setBoardLayout` and `boardRows`. Add a browser regression that selects an account,
switches layouts and grouping, and checks the heading against the visible rows.
Include a fictional symbol held in more than one account.

### 2. Make Performance follow the selected account and window

The page currently presents a whole-portfolio all-time summary, account/window
controls, a second set of metrics, and benchmark metrics with similar visual
weight. Total Return, Realized, Unrealized, and Net Contributed repeat when the
selection is Total/Lifetime. Changing the filter leaves the first summary fixed.

Put account and date controls first, followed by one primary summary for that
selection. Give dollar return and time-weighted percentage return distinct labels;
they describe different calculations. Move the fixed lifetime summary into a
compact, explicitly labeled reference. Place the benchmark chart closer to the
summary and the detailed ratios with Risk. Preserve access to every existing
figure and the explanation that realized/unrealized amounts do not simply sum
to Total Return.

Start in [Performance](../src/dashboard/app/90-performance.js). Check Total,
individual account, custom window, and Returns/Risk states before deciding which
cards can be consolidated. Do not combine returns measured over different spans.

### 3. Use color and type to emphasize meaning

Holdings by Asset applies gain/loss coloring to every numeric column, including
quantity, price, value, and cost basis. That makes ordinary positive balances
compete with actual gains. Use neutral text for these levels; retain signed
green/red treatment for gains, losses, and returns. Keep consistent account/sector
colors where they identify a category.

Give portfolio value a clearer primary position and slightly larger type, with
return and recent change as supporting figures. Use consistent spacing and card
alignment rather than adding more borders. Retain the existing tabular numerals,
contrast, focus rings, signed amounts, and negative-bar hatching.

Start in [Holdings](../src/dashboard/app/10-holdings.js) and
[styles](../src/dashboard/styles.css). Verify neutral levels, gains, losses, zero,
and unavailable values in both table and Board layouts.

### 4. Put scope and date beside the figures they describe

An Overview/Holdings historical date does not change every nearby section.
The top summary remains latest, History retains its own range, and target
allocation/concentration use latest analytics. Tax similarly places a selected
historical year above sections projecting the current year. Existing hints
explain some boundaries, but those explanations are easy to miss while scanning.

Show compact section labels such as "Latest", "As of [date]", "Lifetime", and
"Projected [year]". Show actual measurement dates with filtered return summaries.
Keep essential definitions available through a keyboard/touch disclosure as well
as hover text. Rename the two Holdings sections to distinguish account summaries
from individual positions and lots; make the Board label describe its P&L focus.

Start in the [template](../src/dashboard/template.html), Holdings, and
[Tax](../src/dashboard/app/80-tax.js). These changes must describe the existing
data scope accurately; changing the scope itself needs financial parity checks.

### 5. Reduce the effort of using the dashboard on a phone

The current navigation already scrolls horizontally and all reviewed tabs stay
within the viewport. Wide holdings tables still require substantial horizontal
scrolling. Add a clear overflow cue and consider keeping the identifying column
visible while scrolling. Test lot expansion before making table columns sticky.

Use larger hit areas for compact filters and disclosures on touch devices.
Performance's account and range controls could use a compact account selector
and a short row of common ranges, retaining the remaining choices in a menu.
Keep the selected account and dates visible. Preserve keyboard navigation,
focusable scroll regions, and the existing desktop controls where they work well.

The public demo introduction occupies substantial space on phones. It can be
more compact while keeping its fictional-data designation visible and project
details accessible. This banner is specific to the public demo; it does not
explain the size of the private dashboard header.

### 6. Make common destinations and chart choices easier to find

Move Performance beside Overview and Holdings in the existing navigation. Keep
tab IDs, deep links, keyboard order, and mobile scrolling working together.
The existing Board mode, search, column controls, and collapsible details already
provide useful ways to explore; improve their naming and discoverability.

Overview already offers Lines/Composition and a Net Contributed overlay under
Chart Options. Expose a few common range and chart choices next to the chart.
A simple value-versus-contributions view is a candidate for the default; retain
the current account composition view as an equally reachable choice. Compare
both before changing the default, since composition answers a different question.

Start in [History](../src/dashboard/app/20-history.js) and the template. Keep
contributions visually distinct from investment return.

## Evidence and acceptance

At review time, the Performance benchmark heading appeared roughly 760 pixels
down the desktop page and 1,420 pixels down the phone page. These measurements
include the public demo banner and are observations of the fictional sample,
not targets for every dataset. Twenty tab/viewport states at 1440 and 390 pixels
showed no page-wide overflow or script errors. Design priorities above are
judgments from inspection, not results of user testing.

Implement in small batches, beginning with the layout-switch heading, then
summary hierarchy and color. Exercise
changed filters, historical dates, disclosure state, keyboard/touch access, and
narrow tables against the assembled fictional demo. Keep existing calculations
and scope contracts unless a separately tested behavior change is intended.
Follow the [development loop](../CONTRIBUTING.md#development-loop) and
[screenshot publication rules](PRIVACY.md#before-publishing-a-demo-or-screenshot)
when a batch changes the rendered dashboard.
