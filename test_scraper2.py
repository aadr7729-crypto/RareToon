import scraper
import database as db

db.init_db()

# Test with Season 16 page directly (we know it has codedew links)
s16_url = "https://www.rareanimes.mov/hindi/naruto-shippuden-season-16-episodes-hindi-dubbed-download-hd/"

print("=== Testing get_anime_page_info (Season 16) ===")
info = scraper.get_anime_page_info(s16_url)
print(f"Title: {info.get('title', '')}")
print(f"Status: {info.get('status', '')}")
print(f"Languages: {info.get('languages', [])}")
print(f"Categories: {info.get('categories', [])}")
print(f"WatchMQ URL: {str(info.get('watch_multiquality_url', ''))[:100]}")
print(f"Is season page: {info.get('is_season_page', False)}")
print(f"Episode count: {info.get('episode_count', 0)}")
print(f"All MQ links: {len(info.get('multiquality_links', []))}")
print(f"All Now links: {len(info.get('watch_now_urls', []))}")

print("\n=== Testing get_season_episodes (Season 16) ===")
season_data = scraper.get_season_episodes(s16_url, "hindi")
if isinstance(season_data, dict) and "episodes" in season_data:
    eps = season_data["episodes"]
    print(f"Found {len(eps)} episodes")
    for ep in eps[:3]:
        print(f"  S{ep.season}E{ep.episode} -> {ep.download_url[:80]}")
    if len(eps) > 3:
        print(f"  ... and {len(eps)-3} more")
    if eps:
        # Test resolve_download_url for first episode
        print(f"\n=== Testing resolve_download_url (first episode) ===")
        ep = eps[0]
        print(f"URL: {ep.download_url[:80]}")
        resolved = scraper.resolve_download_url(ep.download_url)
        if "error" in resolved:
            print(f"Error: {resolved['error']}")
        else:
            print(f"Token: {resolved.get('token', '')[:30]}...")
            print(f"Video ID: {resolved.get('video_id', '')}")
            print(f"Episode title: {resolved.get('episode_title', '')}")
            
            print(f"\n=== Testing get_download_links ===")
            links = scraper.get_download_links(
                resolved["token"],
                resolved["links_route"],
                resolved["video_id"]
            )
            if links and not (isinstance(links, dict) and "error" in links):
                for l in links:
                    print(f"  {l.quality} - {l.size}")
                print(f"\nDownload link (720P): {links[1].url[:80] if len(links) > 1 else links[0].url[:80]}")
            else:
                print(f"Error: {links}")
else:
    print(f"Result: {season_data}")

print("\n=== Done ===")
