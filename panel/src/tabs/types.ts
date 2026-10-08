/**
 * The tab registry: the screens, in the order a person meets them.
 *
 * House sits beside Rooms because it is the same question one level up -- what
 * is in this room, what is in the house -- and a person who has just bound a
 * light in a room looks next at what that made available to the whole house.
 *
 * The order is not cosmetic. Overview first is the "is anything wrong" screen,
 * and Dev last is the one that is not for a person's house at all -- it is the
 * workbench where an automation becomes a module and a module becomes
 * automations again, which is the last thing a person picks up; reordering the
 * array reorders the sidebar of the panel to match.
 *
 * `adminOnly` is a UI affordance, not a boundary. The server refuses an
 * admin-only command from a non-admin regardless of what the panel shows (see
 * `protocol.ts`); this flag only decides what is worth rendering.
 */

export interface TabDefinition {
  id: string;
  label: string;
  iconName: string;
  adminOnly: boolean;
  /** The custom element that renders the tab. */
  tag: string;
}

export const TABS: readonly TabDefinition[] = [
  {
    id: "overview",
    label: "Overview",
    iconName: "mdi:view-dashboard-outline",
    adminOnly: false,
    tag: "open-house-tab-overview",
  },
  {
    id: "rooms",
    label: "Rooms",
    iconName: "mdi:floor-plan",
    adminOnly: false,
    tag: "open-house-tab-rooms",
  },
  {
    id: "house",
    label: "House",
    iconName: "mdi:home-outline",
    adminOnly: false,
    tag: "open-house-tab-house",
  },
  {
    id: "profiles",
    label: "Profiles",
    iconName: "mdi:tune-variant",
    adminOnly: false,
    tag: "open-house-tab-profiles",
  },
  {
    id: "store",
    label: "Store",
    iconName: "mdi:storefront-outline",
    adminOnly: true,
    tag: "open-house-tab-store",
  },
  {
    id: "activity",
    label: "Activity",
    iconName: "mdi:history",
    adminOnly: false,
    tag: "open-house-tab-activity",
  },
  {
    id: "health",
    label: "Health",
    iconName: "mdi:heart-pulse",
    adminOnly: false,
    tag: "open-house-tab-health",
  },
  {
    id: "dev",
    label: "Dev",
    iconName: "mdi:flask-outline",
    adminOnly: true,
    tag: "open-house-tab-dev",
  },
] as const;

export type TabId = (typeof TABS)[number]["id"];

/** The tabs a viewer may see, given whether they are an administrator. */
export function tabsFor(admin: boolean): readonly TabDefinition[] {
  return admin ? TABS : TABS.filter((tab) => !tab.adminOnly);
}
