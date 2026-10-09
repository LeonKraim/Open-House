/// The Store's own arithmetic, as a module the hooks load.
///
/// A module's rating, its comment count and its install count are *joins*: they
/// are answers about three collections, and a Store listing that computed them
/// itself would ask the database sixty questions to draw one page. So they are
/// kept as numbers on the module row, and this is what keeps them true.
///
/// **Why a hook at all, and not the client.** The person rating a module is not
/// the person who published it, and the `modules` update rule says only a
/// publisher may edit their own row -- deliberately, because that is what stops
/// a stranger retitling somebody else's module. So the rater cannot be the one
/// to write `stars_sum`, and the count has to be moved by the server that
/// accepted the rating. The alternative was a rule loose enough for anyone to
/// edit any module, which is the one thing a store must not allow.
///
/// **A rating is replaced, not added to.** PocketBase enforces one rating per
/// person per module (`idx_ratings_module_publisher`), so changing your mind is
/// an update. Both paths recompute the sum from the rows rather than adding a
/// delta: a delta applied twice -- a retry, two tabs, a hook that ran on the
/// update and the create -- is a rating of nine out of five, and recomputing
/// cannot drift at all.

/// The sum and the count of one module's ratings, as they are now.
function ratingsOf(app, moduleId) {
  const rows = app.findRecordsByFilter("ratings", "module = {:module}", "", 0, 0, {
    module: moduleId,
  });
  let sum = 0;
  for (const row of rows) sum += row.getInt("stars");
  return { sum: sum, count: rows.length };
}

/// Every count a module row carries, recomputed and saved.
function refresh(app, moduleId) {
  if (!moduleId) return;
  const module = app.findRecordById("modules", moduleId);
  if (!module) return;
  const ratings = ratingsOf(app, moduleId);
  const comments = app.findRecordsByFilter("comments", "module = {:module}", "", 0, 0, {
    module: moduleId,
  });
  const installs = app.findRecordsByFilter("installs", "module = {:module}", "", 0, 0, {
    module: moduleId,
  });
  module.set("stars_sum", ratings.sum);
  module.set("stars_count", ratings.count);
  module.set("comments", comments.length);
  module.set("installs", installs.length);
  app.save(module);
}

/// The module a row is about, whichever collection the row is in.
///
/// A rating, a comment and an install all point at one module, and all three
/// keep their counters the same way; this is the one line that differs between
/// them. It reads the field as a string because that is what a single relation
/// holds -- the id -- and it tolerates the field being absent so that a future
/// collection without one is a no-op rather than a failing hook.
function moduleOf(record) {
  try {
    return record.getString("module");
  } catch (error) {
    return "";
  }
}

module.exports = { ratingsOf, refresh, moduleOf };
