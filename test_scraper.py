import scraper
import database as db

db.init_db()

print("=== Testing search_anime ===")
results = scraper.search_anime("naruto")
print(f"Found {len(results)} results")
for r in results[:5]:
    print(f"  {r.title[:60]} -> {r.url[:80]}")

print("\n=== Testing get_anime_page_info ===")
if results:
    first = results[0]
    info = scraper.get_anime_page_info(first.url)
    print(f"Title: {info.get('title', '')}")
    print(f"Status: {info.get('status', '')}")
    print(f"Languages: {info.get('languages', [])}")
    print(f"Categories: {info.get('categories', [])}")
    print(f"WatchMQ URL: {str(info.get('watch_multiquality_url', ''))[:80]}")
    print(f"Is season page: {info.get('is_season_page', False)}")
    print(f"Episode count: {info.get('episode_count', 0)}")

print("\n=== Testing get_season_episodes ===")
if results and info.get("watch_multiquality_url"):
    season_data = scraper.get_season_episodes(info["watch_multiquality_url"] if info.get("watch_multiquality_url") else first.url)
    if isinstance(season_data, dict) and "episodes" in season_data:
        eps = season_data["episodes"]
        print(f"Found {len(eps)} episodes")
        for ep in eps[:5]:
            print(f"  S{ep.season}E{ep.episode} -> {ep.download_url[:80]}")
        if len(eps) > 5:
            print(f"  ... and {len(eps)-5} more")
    else:
        print(f"Error: {season_data}")

print("\n=== Testing resolve_download_url ===")
if results and info.get("watch_multiquality_url"):
    # Get episodes first
    season_data = scraper.get_season_episodes(first.url)
    if isinstance(season_data, dict) and "episodes" in season_data:
        eps = season_data["episodes"]
        if eps:
            ep = eps[0]
            print(f"Testing with episode: S{ep.season}E{ep.episode}")
            resolved = scraper.resolve_download_url(ep.download_url)
            if "error" in resolved:
                print(f"Error: {resolved['error']}")
            else:
                print(f"Token: {resolved.get('token', '')[:30]}...")
                print(f"Video ID: {resolved.get('video_id', '')}")
                print(f"Links route: {resolved.get('links_route', '')[:60]}...")
                print(f"Episode title: {resolved.get('episode_title', '')}")
                
                # Get download links
                print("\n=== Testing get_download_links ===")
                links = scraper.get_download_links(
                    resolved["token"],
                    resolved["links_route"],
                    resolved["video_id"]
                )
                if links and not (isinstance(links, dict) and "error" in links):
                    for l in links:
                        print(f"  {l.quality} - {l.size} - {l.url[:80]}")
                else:
                    print(f"Error: {links}")
print("\n=== Done ===")
