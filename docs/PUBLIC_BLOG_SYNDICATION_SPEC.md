# Developer Specification: Headless Public Blog Feed & Client Syndication

This document details the architectural specification and implementation guide for syndicating published blog posts from the Mugogo Inc. Engine to client landing pages and external websites via a clean, headless JSON API.

---

## 1. Architectural Overview

```
┌─────────────────────────────────────────────────────────┐
│                    MUGOGO INC. ENGINE                   │
│                                                         │
│   [ Agent / AI ] ──> [ Blog Hub Draft ]                │
│                            │                            │
│                      (Human Approval)                   │
│                            │                            │
│                            ▼                            │
│                  [ Status: "published" ]                │
└────────────────────────────┬────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────┐
│                    FASTAPI BACKEND                      │
│                                                         │
│    GET /api/v1/public/blogs?business_unit_id={BU_ID}    │
│    (With Access-Control-Allow-Origin: * & CDN Caching)  │
└────────────────────────────┬────────────────────────────┘
                             │
                             ▼ (Public Headless JSON Feed)
┌─────────────────────────────────────────────────────────┐
│                     CLIENT WEBSITE                      │
│  (Next.js / WordPress / Webflow / Static HTML Site)     │
│                                                         │
│  Fetches JSON feed & renders articles with native CSS   │
└─────────────────────────────────────────────────────────┘
```

---

## 2. API Endpoints Reference

### A. List Public Syndicated Feed
- **URL**: `GET /api/v1/public/blogs` (also available at `/public/blogs`)
- **Authentication**: None (Unauthenticated Public Endpoint)
- **Query Parameters**:
  - `business_unit_id` (*required*, string): UUID of the client's business unit.
  - `limit` (*optional*, int, default: `10`, max: `50`): Number of posts per page.
  - `offset` (*optional*, int, default: `0`): Pagination offset.
- **Headers Returned**:
  - `Access-Control-Allow-Origin: *` (permits browser-side cross-origin fetching)
  - `Cache-Control: public, max-age=60, s-maxage=300, stale-while-revalidate=600` (browser and CDN caching)

#### Sample Response:
```json
{
  "status": "success",
  "business_unit_id": "8c05e453-9213-4543-9b5a-6fd4525bf460",
  "total_posts": 4,
  "posts": [
    {
      "id": "e441434c-6fa1-49b9-9cf9-d04b3ce5cf0b",
      "business_unit_id": "8c05e453-9213-4543-9b5a-6fd4525bf460",
      "organization_id": "270d6389-c454-46c5-a681-4ce8355ab133",
      "title": "Solar Energy Solutions for Commercial Warehouses",
      "slug": "solar-energy-solutions-commercial-warehouses",
      "summary": "How Kenyan commercial warehouses cut peak demand charges by 65%.",
      "excerpt": "How Kenyan commercial warehouses cut peak demand charges by 65%.",
      "content": "<p>Full article body HTML or markdown rendered here...</p>",
      "content_html": "<p>Full article body HTML or markdown rendered here...</p>",
      "content_markdown": "Full article body HTML or markdown rendered here...",
      "featured_image_url": "https://cdn.mugogoinc.com/blog/solar-commercial.jpg",
      "target_keyword": "commercial solar kenya",
      "author_name": "Mugogo Editorial Team",
      "published_at": "2026-10-12T08:00:00Z"
    }
  ]
}
```

---

### B. Single Article by Slug
- **URL**: `GET /api/v1/public/blogs/{slug}` (also available at `/public/blogs/{slug}`)
- **Query Parameters**:
  - `business_unit_id` (*required*, string): UUID of the business unit.
- **Status Codes**:
  - `200 OK`: Post found and published.
  - `404 Not Found`: Post not found, business unit not found, or article still in draft status.

---

## 3. Client Integration Examples

### Example A: Next.js / React (Client or Server Component)

```tsx
// components/BlogFeed.tsx
import React, { useEffect, useState } from "react";

interface PublicBlogPost {
  id: string;
  title: string;
  slug: string;
  summary: string;
  featured_image_url?: string;
  published_at: string;
}

export default function BlogFeed({ businessUnitId }: { businessUnitId: string }) {
  const [posts, setPosts] = useState<PublicBlogPost[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function fetchBlogs() {
      try {
        const res = await fetch(
          `https://api.mugogoinc.com/api/v1/public/blogs?business_unit_id=${encodeURIComponent(businessUnitId)}`
        );
        const data = await res.json();
        if (data.status === "success") {
          setPosts(data.posts);
        }
      } catch (err) {
        console.error("Failed to load blog posts:", err);
      } finally {
        setLoading(false);
      }
    }
    fetchBlogs();
  }, [businessUnitId]);

  if (loading) return <div className="text-gray-500">Loading latest insights...</div>;
  if (!posts.length) return null;

  return (
    <div className="my-8 grid grid-cols-1 md:grid-cols-3 gap-6">
      {posts.map((post) => (
        <article key={post.id} className="border border-gray-200 rounded-lg overflow-hidden shadow-sm hover:shadow-md transition">
          {post.featured_image_url && (
            <img src={post.featured_image_url} alt={post.title} className="w-full h-48 object-cover" />
          )}
          <div className="p-4">
            <h3 className="font-bold text-lg mb-2">{post.title}</h3>
            <p className="text-gray-600 text-sm line-clamp-3">{post.summary}</p>
            <a 
              href={`/blog/${post.slug}`} 
              className="inline-block mt-4 text-cyan-600 font-semibold text-sm hover:underline"
            >
              Read Article →
            </a>
          </div>
        </article>
      ))}
    </div>
  );
}
```

---

### Example B: Vanilla JavaScript / Static HTML Widget

```html
<!-- Drop this snippet anywhere on a client page -->
<div id="mugogo-blog-container"></div>

<script>
  (async function() {
    const BU_ID = "YOUR_BUSINESS_UNIT_ID_HERE";
    const container = document.getElementById("mugogo-blog-container");

    try {
      const response = await fetch(`https://api.mugogoinc.com/api/v1/public/blogs?business_unit_id=${BU_ID}`);
      const data = await response.json();

      if (data.status === "success" && data.posts.length > 0) {
        container.innerHTML = data.posts.map(post => `
          <div style="margin-bottom: 24px; padding: 16px; border: 1px solid #e5e7eb; border-radius: 8px;">
            <h3 style="font-size: 18px; font-weight: bold; margin-bottom: 8px;">${post.title}</h3>
            <p style="color: #4b5563; font-size: 14px;">${post.summary || ''}</p>
            <a href="/blog/${post.slug}" style="color: #0891b2; font-weight: 600; text-decoration: none;">Read Article →</a>
          </div>
        `).join('');
      }
    } catch (e) {
      console.error("Error loading blog feed:", e);
    }
  })();
</script>
```

---

## 4. Verification & Testing

### 1. Python AST & Lint Check
```bash
python3 -c "import ast; [ast.parse(open(f).read()) for f in ['app/schemas/post.py', 'app/routers/public_blogs.py', 'app/routers/blog.py', 'app/main.py']]; print('AST OK')"
```

### 2. Live API Test
```bash
curl -i -X GET "http://localhost:8000/api/v1/public/blogs?business_unit_id=<BU_ID>"
```
- Verify `HTTP 200 OK`
- Verify `access-control-allow-origin: *` header
- Verify `cache-control: public, max-age=60, s-maxage=300, ...`
- Verify draft articles are **never** returned.
