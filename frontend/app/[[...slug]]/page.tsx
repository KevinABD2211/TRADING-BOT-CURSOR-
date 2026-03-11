import { notFound } from "next/navigation";

/**
 * Catch-all: any path that doesn't match a real page (e.g. /foo, /api/bar on frontend)
 * shows the custom 404 page instead of the host's generic 404.
 */
export default function CatchAllPage() {
  notFound();
}
