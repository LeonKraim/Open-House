/**
 * The Store tab: publish this house's modules, and browse the published Store.
 *
 * Two things are drawn here and the screen keeps them apart. A module this house
 * made is drawn from the house's own module store; a module somebody else
 * published is drawn from the *published* Store -- a server this house has to
 * name. The published section is drawn only once an address exists, because
 * every command under it refuses politely with no address.
 *
 * ## What was here, and is gone
 *
 * The tab used to open on a *bundled* pack index: every pack this repository
 * shipped, filtered by tier, installed by name. Open House no longer ships a
 * corpus -- a house is built from the blueprints a person imports -- so there is
 * nothing to browse and the shelf is retired. A house that wants something new
 * imports it here or installs it from the published Store below.
 *
 * ## The Publish button is always there, and it asks for what it needs
 *
 * The button used to be hidden until a house had both a Store address and a
 * claimed name, on the reasoning that a button whose only outcome is an error is
 * worse than no button. But the person who most needs it is the one who has
 * never set a Store up, and hiding it from them hid the whole idea of publishing.
 * So it is drawn on every module this house made, and pressing it opens a dialog
 * asking for whichever of the two is missing -- the address first, because an
 * address that *changes* forgets the name claimed on the old one -- and then
 * publishes. The same dialog is what the Connect button on the section below
 * opens, so a house with no Store can get one from either end of the tab.
 *
 * The reasoning a walk cannot check lives in the exported helpers below -- the
 * split the filter reads, the rating in words, the star band, the sentence a
 * refusal becomes, and the claim form's own validation -- so `store.test.ts`
 * can pin them without a browser.
 */

import { html, nothing, type TemplateResult } from "lit";
import { OpenHouseElement, formatRelative } from "../base.ts";
import { asPanelError } from "../api/connection.ts";
import { REFUSALS } from "../api/protocol.ts";
import type {
  ModuleOfferRow,
  PublishedRow,
  StoreCommentRow,
  StoreEntry,
  StoreStatus,
} from "../api/models.ts";

const TIER_CHIP: Record<StoreEntry["tier"], string> = {
  official: "ok",
  verified: "ok",
  community: "",
  local: "warn",
};

/**
 * The tier as a chip: one value, one word.
 *
 * The wire's tier is a lower-case key (`official`, `local`); a person reads a
 * capitalised word beside the module, so the label says what the key means. This
 * was once the bundled index's filter as well, and the "all" row it carried is
 * gone with the shelf.
 */
const TIERS: readonly { value: StoreEntry["tier"]; label: string }[] = [
  { value: "official", label: "Official" },
  { value: "verified", label: "Verified" },
  { value: "community", label: "Community" },
  { value: "local", label: "Local" },
];

const TIER_LABELS = new Map(TIERS.map((tier) => [tier.value, tier.label]));

/**
 * The star the rating control and the band are drawn from.
 *
 * A symbol and not an emoji: it is text at the font's size, read by a screen
 * reader as "star" through the button's own label, and it keeps the panel's
 * no-icons-in-words rule.
 */
const STAR = "★";
const OPEN_STAR = "☆";

/**
 * How much prose a published row's blurb holds.
 *
 * The same 400 that `store_api.SUMMARY_LIMIT` holds the field to, and the server
 * is what enforces it -- this is here only so the sentence under the box can say
 * it *before* anything is sent, which is the difference between a person trimming
 * their own words and the server doing it for them.
 */
export const SUMMARY_LIMIT = 400;

/** The two halves of the published list, as `browse` answers them. */
export interface PublishedSplit {
  installed: PublishedRow[];
  not_installed: PublishedRow[];
}

/** Which half of the published list the filter is showing. */
export type StoreSide = "installed" | "not_installed";

/**
 * The two sides of the filter, in the order a person reads them.
 *
 * "Not installed" is the default the tab opens on: the reason to open the
 * published Store at all is to find something to add, and a person who wants
 * what they already have has a chip to say so.
 */
export const STORE_SIDES: readonly { value: StoreSide; label: string }[] = [
  { value: "installed", label: "Installed" },
  { value: "not_installed", label: "Not installed" },
];

/**
 * The published rows the chosen side of the filter shows.
 *
 * The split is the *server's*: "do I have this" is a question about this
 * house's own store of definitions, which the server holds and the screen does
 * not, and the two are matched on the module's slug rather than on the
 * record's id. So the screen's whole part is picking one of the two lists it
 * was handed, which is what this does and all it does.
 */
export function browseRows(split: PublishedSplit, side: StoreSide): PublishedRow[] {
  return side === "installed" ? split.installed : split.not_installed;
}

/**
 * The rating in words, or that nobody has rated it.
 *
 * `rating` is `number | null` and the null is not zero: zero stars is a rating
 * somebody gave, and a screen that drew an unrated module as nought out of five
 * would be saying something no person said. The count is carried in the same
 * sentence because an average with no count beside it ("4.5" of what?) is the
 * number a person actually wants to reason about.
 */
export function ratingLabel(rating: number | null, starsCount: number): string {
  if (rating === null) return "not rated yet";
  const people = starsCount === 1 ? "1 person" : `${starsCount} people`;
  return `${rating.toFixed(1)} from ${people}`;
}

/**
 * The stars as a band, filled to the nearest whole star.
 *
 * A band beside the figure, not instead of it: the number is the average and
 * the band is the shape of it, and a person reads the shape first. Empty for a
 * module nobody has rated, for the same reason `ratingLabel` says so in words
 * rather than drawing nought filled stars -- an empty band is an absence and a
 * filled-out-of-five is an opinion.
 */
export function starBand(rating: number | null): string {
  if (rating === null) return "";
  const filled = Math.max(0, Math.min(5, Math.round(rating)));
  return STAR.repeat(filled) + OPEN_STAR.repeat(5 - filled);
}

/**
 * Whether a person may rate a row, and the hint when they may not.
 *
 * The Store refuses a publisher rating their own module -- an average that
 * included its subject's own vote is a number nobody said -- so the stars are
 * not drawn on the publisher's own rows at all, and the reason is said in a
 * hint rather than left as a click that fails. Returned as one value so the
 * control and its explanation cannot disagree.
 */
export function ratingHint(row: PublishedRow): string | null {
  return row.mine
    ? "The Store does not let a publisher rate their own module."
    : null;
}

/**
 * Why a name may not be claimed, or `null` when it may.
 *
 * The Store refuses a name on its merits before the request -- a space, a
 * capital -- with the same sentence shape it uses for a name somebody already
 * holds, so a person gets one thing to read either way. Catching the mechanical
 * half here means the obvious mistake is answered at once rather than after a
 * round trip; a name the Store keeps for itself is the server's to refuse, and
 * the sentence it sends is shown verbatim.
 */
export function claimProblem(name: string): string | null {
  const wanted = name.trim();
  if (wanted === "") return "Pick a name to publish under.";
  if (/\s/.test(wanted)) {
    return "A publisher name cannot contain spaces; use a dash or an underscore.";
  }
  if (wanted !== wanted.toLowerCase()) {
    return "Publish under a name in lower case; the Store keeps names in lower case.";
  }
  return null;
}

/**
 * What a refused install means to the row it was pressed from.
 *
 * `sentence` is the Store's own words, taken from the refusal rather than
 * composed here: the server is the one that knows whether the name is taken or
 * the request was malformed, and a second copy of that reasoning here could
 * disagree with it. `replace` is whether a module of that name is in the way,
 * which is the one refusal a confirm settles -- the request was answered as
 * invalid (`invalid_format`, the code for a value refused on its merits), and
 * asking again with `replace` is the whole of the answer. A transport failure
 * (`unavailable`) is not that, and gets no Replace button.
 */
export function installRefusal(error: unknown): {
  sentence: string;
  replace: boolean;
} {
  const refusal = asPanelError(error);
  return {
    sentence: refusal.message,
    replace: refusal.code === REFUSALS.invalidFormat,
  };
}

/**
 * Something the setup dialog can ask for.
 *
 * `"address"` is a Store to publish to and `"name"` is the name this house
 * publishes under. They are no longer asked together: the address ships with the
 * build, so the only screen that still asks for one is the Store tab's own
 * Connect, which is for pointing a house at a Store of its own.
 */
export type PublishBlock = "address" | "name";

/**
 * What a publish still asks for, in the order it must be answered. Empty means go.
 *
 * **The address is deliberately not on this list.** It is the address the build
 * ships with (`store.DEFAULT_URL`), which is answered before any screen draws --
 * so asking for it here made a person type out a question the repository had
 * already answered, and made the first thing a publish said be about the Store
 * rather than about their module. A house that wants a Store of its own sets the
 * option instead, which always wins.
 *
 * `null` is a status that has not been read, which is not the same as a missing
 * name: nothing is known to be missing, so nothing is asked for, and the publish
 * itself answers with the server's own sentence if it turns out to be right that
 * something was.
 */
export function publishBlockers(status: StoreStatus | null): PublishBlock[] {
  if (status === null) return [];
  return status.name === "" ? ["name"] : [];
}

export class StoreTab extends OpenHouseElement {
  // The per-module pickers are choices and are invisible to Lit as plain fields
  // (see base.ts). The published Store's state is the same kind of thing -- a
  // chosen filter side, a search, an open comment tray -- and every one of them
  // is a click, so every one of them is declared here.
  static override properties = {
    ...OpenHouseElement.properties,
    isLoading: { state: true },
    error: { state: true },
    busy: { state: true },
    offers: { state: true },
    removing: { state: true },
    moduleReplace: { state: true },
    fileLabel: { state: true },
    notice: { state: true },
    moduleError: { state: true },
    status: { state: true },
    statusError: { state: true },
    claimName: { state: true },
    claimError: { state: true },
    claiming: { state: true },
    setupOpen: { state: true },
    setupOffer: { state: true },
    setupUrl: { state: true },
    setupName: { state: true },
    setupError: { state: true },
    setupBusy: { state: true },
    side: { state: true },
    search: { state: true },
    split: { state: true },
    publishedError: { state: true },
    publishedBusy: { state: true },
    replacing: { state: true },
    installSentence: { state: true },
    commentsFor: { state: true },
    commentsLoading: { state: true },
    comments: { state: true },
    commentDraft: { state: true },
  };

  private isLoading = true;
  private error: ReturnType<OpenHouseElement["toError"]> | null = null;
  private busy: string | null = null;

  /** The modules this house made: what an import saved rather than installed. */
  private offers: ModuleOfferRow[] = [];
  /** Its own error, so a house that cannot answer for its modules still lists packs. */
  private moduleError: ReturnType<OpenHouseElement["toError"]> | null = null;
  /** The module whose Remove button has been armed. */
  private removing: string | null = null;
  /** Whether an imported file may take the place of a module of its name. */
  private moduleReplace = false;
  private fileLabel = "";
  private notice: string | null = null;

  // -- the published Store --------------------------------------------------

  /**
   * Whether this house has a published Store, and under what name.
   *
   * `null` until the read lands. Its failure is kept in `statusError` rather
   * than in `error`, for the reason the modules half keeps its own: a house
   * that cannot reach the Store still has an index and its own modules worth
   * showing, and one banner about a server the person may not have configured
   * would take the whole tab down with it.
   */
  private status: StoreStatus | null = null;
  private statusError: ReturnType<OpenHouseElement["toError"]> | null = null;
  /** What has been typed into the claim form, before it is sent. */
  private claimName = "";
  /** The refusal the Store gave, shown verbatim under the claim form. */
  private claimError: string | null = null;
  private claiming = false;

  /**
   * The setup dialog: open or not, and the module it will publish when it closes.
   *
   * `setupOffer` is `null` when the dialog was opened from the section's Connect
   * button rather than from a module's Publish -- the same two fields, asked for
   * the same reason, but with nothing to publish at the end of it. That is why
   * this is a flag plus a row rather than one nullable field: "closed" and "open
   * with nothing to publish" are different states and a single `null` would spell
   * both of them.
   */
  private setupOpen = false;
  private setupOffer: ModuleOfferRow | null = null;
  /** What has been typed into the setup dialog, before it is sent. */
  private setupUrl = "";
  private setupName = "";
  /**
   * The blurb the published row will carry, prefilled from the module's own
   * description and editable at every publish -- which is the whole reason the
   * dialog opens even when there is nothing to claim.
   */
  private setupSummary = "";
  /** The refusal the Store gave during setup, shown verbatim in the dialog. */
  private setupError: string | null = null;
  private setupBusy = false;
  /** Which half of the published list is showing. Remembered across renders. */
  private side: StoreSide = "not_installed";
  private search = "";
  /** The two lists the server split, or `null` before the first read. */
  private split: PublishedSplit | null = null;
  private publishedError: ReturnType<OpenHouseElement["toError"]> | null = null;
  /** The published row a command is in flight for. */
  private publishedBusy: string | null = null;
  /** The published row whose Replace confirm is armed. */
  private replacing: string | null = null;
  /** The sentence a refused install drew, and the row it belongs to. */
  private installSentence: { id: string; text: string } | null = null;
  /** The published row whose comments are open, and what they were. */
  private commentsFor: string | null = null;
  private commentsLoading = false;
  private comments: StoreCommentRow[] = [];
  private commentDraft = "";

  override connectedCallback(): void {
    super.connectedCallback();
    void this.load();
  }

  reload(): void {
    void this.load();
  }

  /**
   * Read the tab: this house's modules, and the published Store.
   *
   * The published status is *started* before the modules are awaited, so the two
   * reads overlap rather than queue: a house with no Store at all should not wait
   * for its own modules to answer before being told so, and the status read is
   * the one that decides whether anything more on the section is drawn.
   */
  private async load(): Promise<void> {
    this.isLoading = true;
    this.error = null;
    this.requestUpdate();
    const status = this.requireClient()
      .publishedStatus()
      .then(
        (value) => value,
        (error: unknown) => {
          this.statusError = this.toError(error);
          return null;
        },
      );
    try {
      // Asked separately, and its failure kept separate: the modules half is
      // this house's own, and a house that cannot answer for its modules can
      // still be shown the published Store below.
      const store = await this.requireClient().modulesStore();
      this.offers = store.store;
    } catch (error) {
      this.moduleError = this.toError(error);
    }
    this.status = await status;
    if (this.status !== null && this.status.url !== "") {
      await this.loadBrowse();
    }
    this.isLoading = false;
    this.requestUpdate();
  }

  /** The published Store, split as the server split it. */
  private async loadBrowse(): Promise<void> {
    this.publishedError = null;
    try {
      this.split = await this.requireClient().publishedBrowse(this.search);
    } catch (error) {
      this.publishedError = this.toError(error);
    }
  }

  /**
   * Claim this install's publisher name, once.
   *
   * The refusal comes back as a `PanelError` whose message is the sentence to
   * show -- "that name is taken, please pick another name", or the shape
   * refusal -- and it is rendered verbatim rather than reworded here, because
   * the Store's own sentence is the one that says which of the two it was.
   */
  private async submitClaim(): Promise<void> {
    const problem = claimProblem(this.claimName);
    if (problem !== null) {
      this.claimError = problem;
      return;
    }
    this.claiming = true;
    this.claimError = null;
    this.requestUpdate();
    try {
      const reply = await this.requireClient().publisherClaim(this.claimName.trim());
      if (this.status !== null) this.status = { ...this.status, name: reply.name };
      this.notice = `Publishing as ${reply.name}.`;
      await this.loadBrowse();
    } catch (error) {
      this.claimError = asPanelError(error).message;
    } finally {
      this.claiming = false;
      this.requestUpdate();
    }
  }

  /** Run the search the box holds, and redraw the split it answers. */
  private async runSearch(): Promise<void> {
    if (this.status === null || this.status.url === "") return;
    await this.loadBrowse();
    this.requestUpdate();
  }

  /**
   * Install a published module into this house's own store.
   *
   * Nothing runs: it is taken in as a definition like any other, and it is
   * added to a room afterwards the way a local module is. A refusal about a
   * module of that name already being here is answered with its sentence and a
   * Replace confirm, which is the same act asked twice with the intent made
   * explicit -- the shape `publishedInstall`'s own `replace` parameter has.
   */
  private async installPublished(row: PublishedRow, replace: boolean): Promise<void> {
    this.publishedBusy = row.id;
    this.error = null;
    this.notice = null;
    this.installSentence = null;
    this.replacing = null;
    try {
      const reply = await this.requireClient().publishedInstall(row.id, replace);
      this.offers = reply.store;
      this.notice = reply.replaced
        ? `Installed ${reply.module}, replacing the module this house had.`
        : `Installed ${reply.module}. Add it to a room from that room's page.`;
      await this.loadBrowse();
    } catch (error) {
      const refusal = installRefusal(error);
      if (refusal.replace) {
        this.replacing = row.id;
        this.installSentence = { id: row.id, text: refusal.sentence };
      } else {
        this.error = this.toError(error);
      }
    } finally {
      this.publishedBusy = null;
      this.requestUpdate();
    }
  }

  /** Rate a published module, one to five whole stars. */
  private async rate(row: PublishedRow, stars: number): Promise<void> {
    this.publishedBusy = row.id;
    this.error = null;
    this.notice = null;
    try {
      const reply = await this.requireClient().publishedRate(row.id, stars);
      this.patchRow(row.id, { rating: reply.rating, stars_count: reply.stars_count });
      this.notice = `${row.title} is now ${ratingLabel(reply.rating, reply.stars_count)}.`;
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.publishedBusy = null;
      this.requestUpdate();
    }
  }

  /** One published row's comments, read the first time its tray is opened. */
  private async loadComments(row: PublishedRow): Promise<void> {
    this.commentsLoading = true;
    this.comments = [];
    this.commentsFor = row.id;
    this.commentDraft = "";
    this.requestUpdate();
    try {
      this.comments = (await this.requireClient().publishedComments(row.id)).comments;
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.commentsLoading = false;
      this.requestUpdate();
    }
  }

  /** A comment added, answered with the list it now belongs to. */
  private async submitComment(row: PublishedRow): Promise<void> {
    const body = this.commentDraft.trim();
    if (body === "") return;
    this.publishedBusy = row.id;
    this.error = null;
    try {
      const reply = await this.requireClient().publishedComment(row.id, body);
      this.comments = reply.comments;
      this.commentDraft = "";
      this.patchRow(row.id, { comments: reply.comments.length });
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.publishedBusy = null;
      this.requestUpdate();
    }
  }

  /**
   * Publish one of this house's own modules to the Store, by its name.
   *
   * The press opens the dialog rather than sending straight away, because the
   * blurb the Store row carries is a thing to set *at* publish time: a default
   * nobody was shown is a default nobody chose. The dialog also asks for the
   * publisher name, but only until this house has claimed one.
   */
  private publish(offer: ModuleOfferRow): void {
    if (this.status !== null && this.status.url === "") {
      // Nothing to publish to. This is a build that ships no address of its own,
      // and the section above is already asking for one -- so the press says why
      // it did nothing rather than opening a dialog that could only refuse.
      this.error = this.toError(
        new Error("Open House has no Store address to publish to yet."),
      );
      return;
    }
    this.openSetup(offer);
  }

  /** Send a publish, once the dialog has answered what it needed to. */
  private async send(offer: ModuleOfferRow, summary: string): Promise<void> {
    this.busy = offer.slug;
    this.error = null;
    this.notice = null;
    try {
      const reply = await this.requireClient().publishedPublish(offer.slug, summary);
      this.notice =
        `Published ${reply.published.title} to the Store as ` +
        `${reply.published.publisher}. It is on the published list below.`;
      await this.loadBrowse();
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = null;
      this.requestUpdate();
    }
  }

  /** Open the setup dialog, for a module to publish or (`null`) to just connect. */
  private openSetup(offer: ModuleOfferRow | null): void {
    const status = this.status;
    this.setupOpen = true;
    this.setupOffer = offer;
    // Prefilled with what is already known, so a dialog asking for one thing
    // does not blank the other, and so a person who is only changing the blurb
    // sees the description their module already carries rather than an empty box.
    this.setupUrl = status?.url ?? "";
    this.setupName = status?.name ?? "";
    this.setupSummary = offer?.description ?? "";
    this.setupError = null;
  }

  /**
   * The fields the open dialog should ask for.
   *
   * The address alone when the dialog was opened from the section's Connect
   * button: that is a house pointing itself at a Store of its own, which is the
   * one act that changes the address. From a module's Publish, only the name --
   * and only until this house has claimed one.
   */
  private setupFields(): PublishBlock[] {
    return this.setupOffer === null ? ["address"] : publishBlockers(this.status);
  }

  /**
   * Answer the setup dialog: set the address, claim the name, publish.
   *
   * **The order is forced, not chosen.** `publishedConfigure` forgets a claimed
   * name when the address changes, so a name claimed before the address would be
   * thrown away by the address that followed it. Address, then name, then
   * publish -- and each step runs only when the dialog was actually asking for it,
   * so somebody who already holds a name is not made to claim a second one.
   *
   * A refusal anywhere leaves the dialog open with the Store's own sentence in
   * it, because that is the form the person can fix the answer in: "that name is
   * taken, please pick another name" is a thing to retype, not a banner on a
   * screen that has already closed.
   */
  private async submitSetup(): Promise<void> {
    const offer = this.setupOffer;
    const fields = this.setupFields();
    const summary = this.setupSummary.trim();
    this.setupBusy = true;
    this.setupError = null;
    this.requestUpdate();
    try {
      if (fields.includes("address")) {
        this.status = await this.requireClient().publishedConfigure(this.setupUrl.trim());
      }
      if (fields.includes("name")) {
        const problem = claimProblem(this.setupName);
        if (problem !== null) {
          this.setupError = problem;
          return;
        }
        const reply = await this.requireClient().publisherClaim(this.setupName.trim());
        if (this.status !== null) this.status = { ...this.status, name: reply.name };
      }
    } catch (error) {
      this.setupError = asPanelError(error).message;
      return;
    } finally {
      this.setupBusy = false;
    }
    this.setupOpen = false;
    this.setupOffer = null;
    if (offer !== null) {
      await this.send(offer, summary);
    } else {
      this.notice = `Publishing to ${this.status?.url ?? "the Store"}.`;
      await this.loadBrowse();
    }
  }

  /**
   * Whether this house has already put this module on the Store.
   *
   * Read from the rows the Store answered with rather than remembered from the
   * last press, because a press is not the only way to arrive here: a reload, a
   * second tab, and a publish that landed while this one was open all have to
   * agree, and a flag set by a click would agree with none of them. `mine` is the
   * server's own answer -- it expands the publisher and compares it with the id
   * this install holds -- so a module somebody else published under the same name
   * is still somebody else's.
   *
   * A Store that could not be read has no rows, and so reports nothing as
   * published. That leaves the button where it was, which is the failure worth
   * having: a button that is wrong about the Store is worse than one that is
   * merely unhelpful, because the first offers something it cannot do.
   */
  private publishedHere(slug: string): boolean {
    const rows = [
      ...(this.split?.installed ?? []),
      ...(this.split?.not_installed ?? []),
    ];
    return rows.some((row) => row.slug === slug && row.mine);
  }

  /** Rewrite one published row in whichever half of the split it is in. */
  private patchRow(id: string, change: Partial<PublishedRow>): void {
    if (this.split === null) return;
    const apply = (rows: PublishedRow[]): PublishedRow[] =>
      rows.map((row) => (row.id === id ? { ...row, ...change } : row));
    this.split = {
      installed: apply(this.split.installed),
      not_installed: apply(this.split.not_installed),
    };
  }

  /** Stop offering a definition. What is installed from it keeps running. */
  private async removeModule(offer: ModuleOfferRow): Promise<void> {
    this.busy = offer.slug;
    this.error = null;
    this.notice = null;
    try {
      const response = await this.requireClient().modulesRemove(offer.slug);
      this.removing = null;
      this.offers = response.store;
      this.notice =
        response.installed === 0
          ? `${offer.title} is no longer offered.`
          : `${offer.title} is no longer offered. ` +
            `The ${response.installed} ` +
            `${response.installed === 1 ? "room" : "rooms"} running it keep running.`;
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = null;
      this.requestUpdate();
    }
  }

  /** One definition as a file, for a person to give to somebody else. */
  private async download(offer: ModuleOfferRow): Promise<void> {
    this.busy = offer.slug;
    this.error = null;
    this.notice = null;
    try {
      const document = await this.requireClient().modulesExport(offer.slug);
      this.save(document, `${offer.slug}.json`);
      this.notice = `${offer.title} downloaded. Send the file to somebody else to install.`;
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = null;
      this.requestUpdate();
    }
  }

  private save(document: unknown, filename: string): void {
    const blob = new Blob([JSON.stringify(document, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const anchor = globalThis.document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  /**
   * Read somebody else's module file into this house's store.
   *
   * Nothing is installed: reading a file is not consenting to run it, and the
   * file never touches a room. A module this house already offers is refused
   * unless the replace box is ticked, which is why that box is on the screen
   * rather than a default.
   */
  private async onFile(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    this.fileLabel = file.name;
    this.busy = "import";
    this.error = null;
    this.notice = null;
    this.requestUpdate();
    try {
      const document = JSON.parse(await file.text()) as unknown;
      const result = await this.requireClient().modulesImport(
        document,
        this.moduleReplace,
      );
      this.moduleReplace = false;
      this.offers = result.store;
      this.notice = result.replaced
        ? `Imported ${result.imported}, replacing the one this house had.`
        : `Imported ${result.imported}. Install it into any room below.`;
    } catch (error) {
      this.error = this.toError(error);
    } finally {
      this.busy = null;
      // **Cleared, so the same file can be chosen twice.** A `<input type=file>`
      // fires no `change` when it is handed the value it already holds, so
      // without this, re-importing a file somebody has just corrected does
      // nothing at all and looks like the read failed.
      input.value = "";
      this.requestUpdate();
    }
  }

  protected override render(): TemplateResult {
    if (
      this.isLoading &&
      this.offers.length === 0 &&
      this.split === null &&
      this.status === null
    ) {
      return this.loading("Reading the Store...");
    }
    return html`
      ${this.errorBanner(this.error)}
      ${this.notice ? html`<div class="banner info">${this.notice}</div>` : null}
      <h1>Store</h1>
      ${this.renderSetup()} ${this.renderPublished()} ${this.renderModules()}
    `;
  }

  // -- the published Store --------------------------------------------------

  /**
   * The published Store as one section: the status line, the publisher's name,
   * and the filtered list under it.
   *
   * The whole section is behind the address: with no Store named there is
   * nothing under it that could be asked, so one quiet sentence says so and
   * where to set one. Its failure is drawn on its own, without taking the pack
   * index or the house's own modules down with it -- the same rule the modules
   * half already follows.
   */
  private renderPublished(): TemplateResult | typeof nothing {
    if (this.statusError) {
      return html`<section class="card">
        <h2>The Store</h2>
        ${this.errorBanner(this.statusError)}
        <p class="help">
          The modules below and the pack index are unaffected.
        </p>
      </section>`;
    }
    const status = this.status;
    if (status === null) return html``;
    if (status.url === "") {
      return this.renderConnect();
    }
    return html`${this.renderPublisher()} ${this.renderBrowse()}`;
  }

  /**
   * The section before this house has a Store to talk to.
   *
   * One sentence and one button that opens the address form, which is the one
   * place in the panel an address is still asked for -- a house pointing itself
   * at a Store of its own, which is the only thing that changes it. The sentence
   * under the button names the Configure screen too, because a house with no
   * modules of its own has nothing on this tab to start from.
   */
  private renderConnect(): TemplateResult | typeof nothing {
    // The dialog it opens asks for the same address, and a section asking for a
    // thing beside a dialog asking for it reads as the dialog having failed.
    if (this.setupOpen) return nothing;
    return html`<section class="card">
      <h2>The Store</h2>
      <p class="help">
        No Store is configured yet. Publishing your own modules, and installing
        other people's, needs one: give Open House its address and it will
        remember it.
      </p>
      <button
        type="button"
        class="primary"
        id="store-connect"
        @click=${() => this.openSetup(null)}
      >
        Connect a Store
      </button>
      <p class="help">
        The same address can be set in
        <em
          >Settings &rarr; Devices &amp; Services &rarr; Open House &rarr;
          Configure</em
        >.
      </p>
    </section>`;
  }

  /** Whether the open dialog's questions have been answered enough to submit. */
  private get setupAnswered(): boolean {
    const fields = this.setupFields();
    if (fields.includes("address") && this.setupUrl.trim() === "") return false;
    if (fields.includes("name") && this.setupName.trim() === "") return false;
    return true;
  }

  /**
   * The setup dialog: ask for the Store, the name, or both, then publish.
   *
   * Drawn from `setupOpen` rather than from a click, so it survives every
   * re-render a command landing in the middle of it causes -- including the one
   * that reports a refusal, which is the render the person then has to correct.
   * The fields are the ones `setupFields` says are missing and nothing else, so
   * somebody who already holds a name is asked only for the address.
   */
  private renderSetup(): TemplateResult | typeof nothing {
    if (!this.setupOpen) return nothing;
    const fields = this.setupFields();
    const publishing = this.setupOffer !== null;
    return html`<open-house-dialog
      .heading=${publishing
        ? `Publish "${this.setupOffer?.title ?? ""}"`
        : "Connect a Store"}
      .open=${true}
      @dialog-closed=${() => {
        this.setupOpen = false;
        this.setupOffer = null;
      }}
    >
      <p class="help">
        ${publishing
          ? html`The description below is what the Store shows beside this
              module. It is asked every time, so the blurb can be changed.`
          : html`Open House will remember this address, and use it for every
              module you publish to the Store and install from it.`}
      </p>
      ${publishing ? this.renderSummaryField() : nothing}
      ${fields.includes("address")
        ? html`<div class="field">
            <div class="label-row">
              <span class="label">Store address</span>
            </div>
            <input
              type="text"
              id="store-setup-url"
              autocomplete="off"
              placeholder="https://store.example"
              .value=${this.setupUrl}
              ?disabled=${this.setupBusy}
              @input=${(event: Event) => {
                this.setupUrl = (event.target as HTMLInputElement).value;
              }}
            />
          </div>`
        : nothing}
      ${fields.includes("name")
        ? this.renderNameField({
            id: "store-setup-name",
            value: this.setupName,
            disabled: this.setupBusy,
            set: (name) => {
              this.setupName = name;
            },
          })
        : nothing}
      <div class="row">
        <button
          type="button"
          class="primary"
          id="store-setup-submit"
          ?disabled=${this.setupBusy || !this.setupAnswered}
          @click=${() => void this.submitSetup()}
        >
          ${this.setupBusy
            ? "Working..."
            : publishing
              ? "Publish"
              : "Connect"}
        </button>
        <button
          type="button"
          id="store-setup-cancel"
          @click=${() => {
            this.setupOpen = false;
            this.setupOffer = null;
          }}
        >
          Cancel
        </button>
      </div>
      ${this.setupError
        ? html`<p class="help" role="alert">${this.setupError}</p>`
        : nothing}
    </open-house-dialog>`;
  }

  /**
   * The blurb the published row will carry.
   *
   * Prefilled from the module's own description, and editable, because the two
   * are written for different readers: a module's description is a note to the
   * house that owns it, and the row's summary is what a stranger reads before
   * deciding to install somebody's work. Editing this changes the row alone --
   * the description travels whole inside the published document, which is what
   * anybody installing it reads afterwards.
   */
  private renderSummaryField(): TemplateResult {
    return html`<div class="field">
      <div class="label-row">
        <span class="label">Description</span>
      </div>
      <textarea
        id="store-setup-summary"
        rows="3"
        aria-label="Description for the Store"
        .value=${this.setupSummary}
        ?disabled=${this.setupBusy}
        @input=${(event: Event) => {
          this.setupSummary = (event.target as HTMLTextAreaElement).value;
        }}
      ></textarea>
      <p class="help">
        The Store shows this beside the module. Past ${SUMMARY_LIMIT} characters
        it is trimmed on the row; the description the module carries travels
        whole with the file.
      </p>
    </div>`;
  }

  /**
   * The publisher-name field, with the input's id and state given by the caller.
   *
   * One field, two places: the section's own claim form and the setup dialog both
   * ask for a name, and two live inputs sharing one id is an invalid document
   * that a `querySelector` answers unpredictably. So the id travels as an
   * argument and the markup is written once.
   */
  private renderNameField(field: {
    id: string;
    value: string;
    disabled: boolean;
    set: (name: string) => void;
  }): TemplateResult {
    return html`<div class="field">
      <div class="label-row">
        <span class="label">Publisher name</span>
      </div>
      <input
        type="text"
        id=${field.id}
        autocomplete="off"
        placeholder="marqbarq"
        .value=${field.value}
        ?disabled=${field.disabled}
        @input=${(event: Event) =>
          field.set((event.target as HTMLInputElement).value)}
      />
    </div>`;
  }

  /**
   * The name this install publishes under: a fact once claimed, a form until.
   *
   * Set once per install and not offered for change, because that is the
   * requirement the Store enforces -- a name belongs to whoever claimed it
   * first, and a screen with a Rename button would be offering an act the
   * server refuses. The sentence under it says the thing a person cannot
   * otherwise see: this is what their modules carry as their author.
   */
  private renderPublisher(): TemplateResult | typeof nothing {
    // The setup dialog is asking the same question while it is open -- what name
    // does this house publish under -- and two live boxes for one question is
    // the question twice: the box behind the dialog is the one that gets read as
    // "did that not work?". So the card stands down until the dialog is gone.
    if (this.setupOpen && this.setupFields().includes("name")) return nothing;
    const status = this.status;
    if (status !== null && status.name !== "") {
      return html`<p class="muted small">
        Publishing as <code>${status.name}</code>. This is the author name the
        modules you publish show in the Store.
      </p>`;
    }
    return html`<section class="card">
      <h2>Choose a publisher name</h2>
      <p class="help">
        A name is claimed once for this house and cannot be changed afterwards,
        and it is what the modules you publish show as their author. If somebody
        has already claimed the name you want, the Store will say so.
      </p>
      ${this.renderNameField({
        id: "store-claim-name",
        value: this.claimName,
        disabled: this.claiming,
        set: (name) => {
          this.claimName = name;
        },
      })}
      <button
        type="button"
        class="primary"
        id="store-claim-submit"
        ?disabled=${this.claiming}
        @click=${() => void this.submitClaim()}
      >
        ${this.claiming ? "Claiming..." : "Publish as this name"}
      </button>
      ${this.claimError
        ? html`<p class="help" role="alert">${this.claimError}</p>`
        : nothing}
    </section>`;
  }

  /**
   * The published list, with the two-way filter above it.
   *
   * The filter is the point of this half: a person is either looking for what
   * they do not have or checking what they do, and the split the server sends
   * is exactly those two lists. The search box narrows both and is offered
   * because a long list needs one, not because the filter depends on it.
   */
  private renderBrowse(): TemplateResult {
    const rows = this.split === null ? [] : browseRows(this.split, this.side);
    return html`<section class="card">
      <div class="row spread wrap">
        <h2>The Store</h2>
        <div class="row wrap">
          <div class="field">
            <input
              type="text"
              id="store-published-search"
              placeholder="Search the Store"
              aria-label="Search the published Store"
              .value=${this.search}
              @input=${(event: Event) => {
                this.search = (event.target as HTMLInputElement).value;
              }}
              @keydown=${(event: KeyboardEvent) => {
                if (event.key === "Enter") void this.runSearch();
              }}
            />
          </div>
          <button type="button" @click=${() => void this.runSearch()}>Search</button>
        </div>
      </div>
      <div class="row wrap" role="tablist" aria-label="Published modules">
        ${STORE_SIDES.map(
          (side) => html`<button
            type="button"
            class="${this.side === side.value ? "primary" : ""}"
            id="store-side-${side.value}"
            role="tab"
            aria-selected=${this.side === side.value}
            @click=${() => {
              this.side = side.value;
            }}
          >
            ${side.label}
          </button>`,
        )}
      </div>
      ${this.publishedError ? this.errorBanner(this.publishedError) : nothing}
      ${this.split === null
        ? html`<p class="muted">Reading the Store...</p>`
        : rows.length === 0
          ? html`<p class="muted">
              ${this.side === "installed"
                ? "You have not installed anything from the Store yet."
                : "Nothing to install right now."}
            </p>`
          : html`<div class="stack">
              ${rows.map((row) => this.renderPublishedRow(row))}
            </div>`}
    </section>`;
  }

  private renderPublishedRow(row: PublishedRow): TemplateResult {
    const busy = this.publishedBusy === row.id;
    const sentence =
      this.installSentence?.id === row.id ? this.installSentence.text : null;
    const replaceArmed = this.replacing === row.id;
    // Addressed by the Store's record id rather than by position, so a walk
    // that clicked "the second Install button" is not broken by a new row
    // arriving above it.
    return html`<div class="nested" id="store-published-${row.id}">
      <div class="row spread wrap">
        <div class="grow">
          <h3>${row.title}</h3>
          <p class="muted small">
            ${row.publisher} &middot; v${row.version}
            <span class="chip ${TIER_CHIP[row.tier]}"
              >${TIER_LABELS.get(row.tier) ?? row.tier}</span
            >
          </p>
        </div>
      </div>
      ${row.review ? html`<p class="help">${row.review}</p>` : nothing}
      <p>${row.summary}</p>
      <p class="muted small">
        ${row.installs} ${row.installs === 1 ? "install" : "installs"} &middot;
        ${row.comments} ${row.comments === 1 ? "comment" : "comments"} &middot;
        ${ratingLabel(row.rating, row.stars_count)}
        ${row.rating === null
          ? nothing
          : html` <span aria-hidden="true">${starBand(row.rating)}</span>`}
      </p>
      <div class="row wrap" style="margin-top:8px">
        ${row.mine
          ? html`<span class="chip">yours</span>`
          : this.side === "not_installed"
            ? html`<button
                type="button"
                class="primary"
                id="store-install-${row.id}"
                ?disabled=${busy}
                @click=${() => void this.installPublished(row, false)}
              >
                ${busy ? "Installing..." : "Install"}
              </button>`
            : html`<span class="chip ok">installed</span>`}
        ${row.mine
          ? html`<span class="muted small">${ratingHint(row)}</span>`
          : html`<span class="row wrap" role="group" aria-label="Rate this module">
              ${[1, 2, 3, 4, 5].map(
                (stars) => html`<button
                  type="button"
                  class="icon"
                  title="Rate ${stars} star${stars === 1 ? "" : "s"}"
                  aria-label="Rate ${stars} star${stars === 1 ? "" : "s"}"
                  ?disabled=${busy}
                  @click=${() => void this.rate(row, stars)}
                >
                  ${STAR}
                </button>`,
              )}
            </span>`}
      </div>
      ${sentence ? html`<p class="help" role="alert">${sentence}</p>` : nothing}
      ${replaceArmed
        ? html`<div class="row wrap">
            <button
              type="button"
              class="danger"
              id="store-replace-${row.id}"
              ?disabled=${busy}
              @click=${() => void this.installPublished(row, true)}
            >
              Replace it
            </button>
            <button
              type="button"
              @click=${() => {
                this.replacing = null;
                this.installSentence = null;
              }}
            >
              Cancel
            </button>
          </div>`
        : nothing}
      ${this.renderComments(row)}
    </div>`;
  }

  /**
   * One row's comments, folded away until somebody opens them.
   *
   * Nothing is read until the tray is opened, because a page of twenty modules
   * is twenty conversations nobody asked to see. The count on the summary is
   * what says there is anything in there at all.
   */
  private renderComments(row: PublishedRow): TemplateResult {
    const open = this.commentsFor === row.id;
    return html`<details
      id="store-comments-${row.id}"
      @toggle=${(event: Event) => {
        const details = event.target as HTMLDetailsElement;
        if (details.open && this.commentsFor !== row.id) {
          void this.loadComments(row);
        }
      }}
    >
      <summary>
        ${row.comments === 0
          ? "Comments"
          : `${row.comments} ${row.comments === 1 ? "comment" : "comments"}`}
      </summary>
      ${open
        ? html`
            ${this.comments.length === 0
              ? html`<p class="muted">Nobody has said anything yet.</p>`
              : html`<div class="stack">
                  ${this.comments.map(
                    (comment) => html`<div class="nested">
                      <p class="muted small">
                        ${comment.publisher} &middot;
                        ${formatRelative(comment.created)}
                      </p>
                      <p>${comment.body}</p>
                    </div>`,
                  )}
                </div>`}
            <div class="field">
              <div class="label-row">
                <span class="label">Add a comment</span>
              </div>
              <textarea
                rows="3"
                aria-label="Add a comment"
                .value=${this.commentDraft}
                ?disabled=${this.publishedBusy === row.id}
                @input=${(event: Event) => {
                  this.commentDraft = (event.target as HTMLTextAreaElement).value;
                }}
              ></textarea>
            </div>
            <button
              type="button"
              ?disabled=${this.publishedBusy === row.id || this.commentDraft.trim() === ""}
              @click=${() => void this.submitComment(row)}
            >
              Say it
            </button>
          `
        : this.commentsLoading
          ? html`<p class="muted small">Reading the comments...</p>`
          : nothing}
    </details>`;
  }

  // -- the modules this house made ------------------------------------------

  /**
   * The store's local half: modules a person imported and saved.
   *
   * These are not packs -- a pack is a catalog's, and installing one is picking
   * it out of the index. A module is this house's own, and *installing* it means
   * nothing more than keeping it: it is a file in this house's store, offered
   * here as something you have. Putting it in a room is the other act, and it
   * belongs where the room is -- the room's own page, under *Add module to
   * room*. The two are different things and the screen keeps them apart: this
   * one says what you have and where each copy is running, and never moves one.
   */
  private renderModules(): TemplateResult {
    return html`<section class="card">
      <div class="row spread wrap">
        <div class="grow">
          <h2>Modules you made</h2>
          <p class="help">
            What the Dev tab saved. A module is yours to keep: add it to a room
            from that room's page, or to the whole house from the House page, as
            many times as you like -- each room gets its own copy and its own
            outputs. A module file is self-contained: somebody else needs
            neither the blueprint nor the network to install it.
          </p>
        </div>
      </div>
      ${this.moduleError
        ? html`<div class="banner warn">
            Your modules could not be read: ${this.moduleError.message} The pack
            index below is unaffected.
          </div>`
        : nothing}
      ${this.offers.length === 0
        ? html`<p class="muted">
            None yet. Import an automation or a blueprint in the Dev tab and save
            it as a module, or import a file somebody gave you below.
          </p>`
        : html`<div class="stack">
            ${this.offers.map((offer) => this.renderModule(offer))}
          </div>`}
      ${this.renderImport()}
    </section>`;
  }

  private renderModule(offer: ModuleOfferRow): TemplateResult {
    const busy = this.busy === offer.slug;
    const armed = this.removing === offer.slug;
    // Addressed by slug rather than by position: a store row is one module, and
    // a walk that clicked "the second Remove button" would be a walk that broke
    // the day a module was added above it.
    return html`<div class="nested" id="store-module-${offer.slug}">
      <div class="row spread wrap">
        <div class="grow">
          <h3>${offer.title}</h3>
          <p class="muted small">
            <code>${offer.slug}</code>
            ${offer.author ? html` &middot; ${offer.author}` : null}
            &middot; v${offer.version} &middot; ${offer.licence}
          </p>
        </div>
        <div class="row">
          <span class="chip ok">installed</span>
          ${offer.pinned
            ? html`<span
                class="chip warn"
                title="This module names devices from this house, so it would install somewhere else only if that house holds the same devices."
              >
                your devices
              </span>`
            : html`<span class="chip">any house</span>`}
        </div>
      </div>
      <p>${offer.description}</p>
      <p class="help">
        ${offer.blueprint ? html`From ${offer.blueprint}. ` : null}
        ${offer.slots.length === 0
          ? "Reaches through no slots."
          : html`Reaches through ${offer.slots.join(", ")}.`}
      </p>
      <p class="help">
        ${offer.deployed.length === 0
          ? html`In no room yet. Add it to one from that room's page --
              <em>Add module to room</em>.`
          : html`In ${offer.deployed.map(
              (where, index) => html`${index > 0 ? ", " : ""}${where.room_name}
                ${where.running
                  ? null
                  : html`<span class="chip warn">not running</span>`}`,
            )}.`}
      </p>
      <div class="row wrap" style="margin-top:8px">
        <button
          type="button"
          id="store-download-${offer.slug}"
          ?disabled=${busy}
          @click=${() => void this.download(offer)}
        >
          Download the file
        </button>
        ${this.publishedHere(offer.slug)
          ? html`<button type="button" id="store-published-${offer.slug}" disabled>
              Published
            </button>`
          : html`<button
              type="button"
              class="primary"
              id="store-publish-${offer.slug}"
              ?disabled=${busy}
              @click=${() => void this.publish(offer)}
            >
              ${busy ? "Publishing..." : "Publish"}
            </button>`}
        ${armed
          ? html`<button
                type="button"
                class="danger"
                ?disabled=${busy}
                @click=${() => void this.removeModule(offer)}
              >
                Yes, stop offering it
              </button>
              <button type="button" @click=${() => (this.removing = null)}>
                Cancel
              </button>`
          : html`<button
              type="button"
              id="store-remove-${offer.slug}"
              @click=${() => (this.removing = offer.slug)}
            >
              Remove
            </button>`}
      </div>
      ${armed
        ? html`<p class="help">
            Removing this stops it being offered. Anything already installed from
            it keeps its own copy of the document and keeps running -- unbind the
            room it is in to stop that.
          </p>`
        : nothing}
    </div>`;
  }

  private renderImport(): TemplateResult {
    return html`<div class="nested">
      <h3>Install a module somebody gave you</h3>
      <p class="help">
        A <code>.json</code> file from this panel's Download. It carries the
        blueprint and the answers, so nothing else is needed. Importing only
        adds it here -- it is installed into a room afterwards, above.
      </p>
      <div class="field">
        <div class="label-row">
          <span class="label">Import a module file</span>
        </div>
        <input
          type="file"
          accept="application/json,.json"
          aria-label="Choose a module file"
          ?disabled=${!this.admin || this.busy === "import"}
          @change=${(event: Event) => void this.onFile(event)}
        />
      </div>
      <label class="toggle">
        <input
          type="checkbox"
          .checked=${this.moduleReplace}
          @change=${(event: Event) => {
            this.moduleReplace = (event.target as HTMLInputElement).checked;
          }}
        />
        <span>Replace a module this house already offers by that name</span>
      </label>
      ${this.fileLabel
        ? html`<p class="muted small">
            ${this.busy === "import" ? "Reading" : "Last file:"} ${this.fileLabel}
          </p>`
        : nothing}
    </div>`;
  }

}

if (!customElements.get("open-house-tab-store")) {
  customElements.define("open-house-tab-store", StoreTab);
}
