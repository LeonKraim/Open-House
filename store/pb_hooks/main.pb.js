/// What the Store does when a rating, a comment or an install lands.
///
/// The arithmetic itself is in `store.js`; what is here is only the wiring --
/// which collections move a counter, and in which direction. Three collections
/// times the events worth reacting to is nine handlers, and each of them is the
/// same three lines, so they are written once each rather than folded into a
/// loop: a store's counters are worth being able to read at a glance.
///
/// **Why each handler loads the module itself.** PocketBase serializes a hook
/// handler and evaluates it in a fresh runtime, so a function or a constant
/// declared at the top of this file -- or even `require`d at the top of it -- is
/// not in scope when the handler runs. `require` *inside* the handler is, and
/// that is the only supported way to share code between a hook and its helpers.
/// The symptom of getting this wrong is not a load error but a request that
/// fails after the row was already written, which is worth knowing before
/// debugging it a second time.
///
/// **Deleting counts, too.** A comment that is removed is a comment that is
/// gone, and an install that is undone is a house that no longer has the
/// module: leaving the numbers up would make the Store's totals a count of what
/// was ever done rather than what is true now.

onRecordAfterCreateSuccess((e) => {
  const store = require(`${__hooks}/store.js`);
  store.refresh(e.app, store.moduleOf(e.record));
  e.next();
}, "ratings");

onRecordAfterUpdateSuccess((e) => {
  const store = require(`${__hooks}/store.js`);
  store.refresh(e.app, store.moduleOf(e.record));
  e.next();
}, "ratings");

onRecordAfterDeleteSuccess((e) => {
  const store = require(`${__hooks}/store.js`);
  store.refresh(e.app, store.moduleOf(e.record));
  e.next();
}, "ratings");

onRecordAfterCreateSuccess((e) => {
  const store = require(`${__hooks}/store.js`);
  store.refresh(e.app, store.moduleOf(e.record));
  e.next();
}, "comments");

onRecordAfterDeleteSuccess((e) => {
  const store = require(`${__hooks}/store.js`);
  store.refresh(e.app, store.moduleOf(e.record));
  e.next();
}, "comments");

onRecordAfterCreateSuccess((e) => {
  const store = require(`${__hooks}/store.js`);
  store.refresh(e.app, store.moduleOf(e.record));
  e.next();
}, "installs");

onRecordAfterDeleteSuccess((e) => {
  const store = require(`${__hooks}/store.js`);
  store.refresh(e.app, store.moduleOf(e.record));
  e.next();
}, "installs");
