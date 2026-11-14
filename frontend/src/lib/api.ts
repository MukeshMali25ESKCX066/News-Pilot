export type Source = {
  id: number;
  name: string;
  url: string;
  source_type: string;
  category: string | null;
  active: boolean | number;
};

export type NewsEntry = {
  id: number;
  title: string;
  summary: string | null;
  url: string | null;
  published_at: string | null;
  source_name: string;
  source_category: string | null;
};

export type StoryItem = {
  id: number;
  title: string;
  summary: string | null;
  url: string | null;
};

export type Story = {
  title: string;
  summary: string;
  score: number;
  items: StoryItem[];
};

export type Article = {
  id: number;
  title: string;
  summary: string | null;
  body?: string | null;
  language: string;
  status: string;
  created_at: string;
  seo_title: string | null;
  slug: string | null;
  meta_description: string | null;
  focus_keyphrase: string | null;
  tags: string[];
  wordpress_post_id: number | null;
  wordpress_edit_url: string | null;
  wordpress_status: string | null;
};

export type WordPressStatus = {
  connected: boolean;
  site_url?: string;
  error?: string;
};

export type AppSettings = WordPressStatus & {
  auto_publish: boolean;
};

export type PublishResult = {
  status: string;
  wordpress_post_id: number;
  wordpress_edit_url: string;
  wordpress_status?: string | null;
};

export type GeneratedArticleResult = {
  article_id: number;
  related_count?: number;
  source_count?: number;
  automatic_publish?: { status: string; error?: string } | null;
};

export type CollectionResult = {
  new_items: number;
  errors: Array<{ source: string; error: string }>;
};

export async function apiRequest<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`/api${path}`, {
    ...options,
    headers,
    cache: "no-store",
  });

  if (!response.ok) {
    const body = await response.text();
    let message = body || `Request failed (${response.status})`;
    if (response.headers.get("content-type")?.includes("application/json")) {
      try {
        const payload: unknown = JSON.parse(body);
        if (
          typeof payload === "object" &&
          payload !== null &&
          "detail" in payload &&
          typeof payload.detail === "string"
        ) {
          message = payload.detail;
        }
      } catch (error) {
        if (!(error instanceof SyntaxError)) throw error;
      }
    }
    throw new Error(message);
  }

  return (await response.json()) as T;
}
