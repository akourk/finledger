// Browser tests use the same visible controls as a person on each viewport.
'use strict';
async function selectSection(surface, name) {
  const mobile = await surface.evaluate(() => matchMedia('(max-width: 720px)').matches);
  if (!mobile) return surface.click('#tabbtn-' + name);
  if (['overview', 'holdings', 'performance'].includes(name)) {
    return surface.click('#mobile-navigation [data-mobile-tab="' + name + '"]');
  }
  await surface.click('#mobile-more');
  await surface.click('#mobile-section-list [data-mobile-tab="' + name + '"]');
}
module.exports = {selectSection};
