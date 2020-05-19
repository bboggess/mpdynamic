import mpd
import spotipy
import threading
import socket

class Track:
	'''
	Each external library has its own internal implentation of a song. This
	is a universal adapter for them, retaining only the information needed to
	move between formats.
	'''
	def __init__(self, title, artist, album):
		self.title = title
		self.artist = artist
		self.album = album

	def find(self, client):
		matches = client.find("title", self.title)
		return next((match for match in matches if match['artist'] == self.artist), None)

# Spotify auth
SPOTIFY_ID = 'cbf58f659c8f47ff86867aae927b650f'
SPOTIFY_SECRET = 'b4d44856d63141b8976932f612ae4614'

# MPD connection info (MOVE TO CONFIG FILE)
# host = '192.168.0.2'
host = '127.0.0.1'
port = 6600

# Once the number of remaining tracks in playlist drops below this value,
# start searching for more tracks to queue
THRESHOLD = 10

# Tracks that have been fetched from Spotify, but not yet added to the MPD
# playlist
similar_queue = []
# Lock variables -- I had some bizarre bugs before I remembered to lock 
# all access to MPD client!
queue_lock = threading.Condition()
mpd_lock = threading.Condition()

# Functions for converting to internal Track object
def track_from_spotify(spotify_track):
	title = spotify_track['name']
	artist = spotify_track['artists'][0]['name']
	album = spotify_track['album']['name']
	return Track(title, artist, album)

def track_from_mpd(mpd_track):
	title = mpd_track['title']
	artist = mpd_track['artist']
	album = mpd_track['album']
	return Track(title, artist, album)

def fetch_cur_track(mpd, network):
	'''
	Get now playing on MPD (TODO: what happens if not playing?) and find
	track ID from Spotify
	'''
	local = track_from_mpd(mpd.currentsong())
	return spotify_search(network, local)

def queue_similar(mpd, network):
	'''
	Searches Spotify for tracks similar to the currently playing one. The
	tracks present in the local library are then added to the MPD playlist.
	'''
	cur = fetch_cur_track(mpd, network)
	similars = spotify_recs(mpd, network, [cur])
	for track in similars:
		with mpd_lock:
			mpd_track = find_local_track(mpd, track)
		if mpd_track:
			with queue_lock: 
				similar_queue.append(mpd_track)
				queue_lock.notifyAll()

def add_track(mpd, track):
	'''
	Adds an MPD track object to the current playlist
	'''
	while True:
		with mpd_lock:
			try:
				mpd.add(track['file'])
				break
			except socket.timeout:
				mpd = init_mpd(addr, port)
			except BaseException as e:
				print(str(e))
				print(track)
				break

def init_spotify():
	creds = spotipy.SpotifyClientCredentials(SPOTIFY_ID, SPOTIFY_SECRET)
	return spotipy.Spotify(client_credentials_manager=creds)

def spotify_recs(mpd, spotify, ids):
	'''
	Takes a list of up to five Spotify track IDs, and returns a list of
	suggested tracks.
	'''
	recs = spotify.recommendations(seed_tracks=ids)['tracks']
	recs = map(lambda x: track_from_spotify(x), recs)
	return filter(lambda x: have_artist(mpd, x.artist), recs)

def spotify_search(spotify, track):
	results = spotify.search(q = track.title, type = 'track')
	results = results['tracks']['items']
	for result in results:
		if result['artists'][0]['name'] == track.artist:
			return result['id']
	return None

def init_mpd(addr, port):
	'''
	Connects to MPD client
	'''
	client = mpd.MPDClient()
	client.timeout = 10
	client.idletimeout = None
	client.connect(addr, port)
	return client

def shutdown(mpd):
	'''
	Closes connection to MPD
	'''
	with mpd_lock:
		mpd.close()
		mpd.disconnect()

def have_artist(mpd, artist_name):
	'''
	Checks whether the artist is present in the MPD library.
	'''
	result = False
	while True:
		with mpd_lock:
			try:
				result = mpd.count('artist', artist_name)['songs'] > '0'
				break
			except socket.timeout:
				init_mpd(host, port)
			except KeyError as e:
				print(str(e))
				result = False
				break
	return result

def find_local_track(mpd, track):
	'''
	Takes in a Track object and searches for the track in the
	local library. If found, returns corresponding MPD track object.
	Otherwise, return None.
	'''
	with mpd_lock:
		matches = mpd.find("title", track.title)
	return next((match for match in matches if match['artist'] == track.artist), None)

def mpd_songs_remaining(mpd):
	'''
	Calculates the number of songs remanining on current playlist
	'''
	with mpd_lock:
		status = mpd.status()
	i = int(status['song']) if 'song' in status.keys() else 0
	return int(status['playlistlength']) - i

def previous_five_songs(mpd):
	with mpd_lock:
		status = mpd.status()
		playlist = mpd.playlistinfo()
	i = int(status['nextsong']) - 5 if int(status['nextsong']) >= 5 else 0
	j = i + 5 if i < int(status['playlistlength']) - 5 else int(status['playlistlength'])
	return map(lambda x: track_from_mpd(x), playlist[i:j])

def queue_main(mpd):
	'''
	Main function for the queue thread. Whenever there are new tracks added to
	the queue from the Spotify search, adds them to the MPD playlist
	'''
	while True:
		with queue_lock: 
			if len(similar_queue) > 0:
				add_track(mpd, similar_queue.pop(0))
			else:
				queue_lock.wait()

def main():
	mpd = init_mpd(host, port)
	spotify = init_spotify()

	mpd_thread = threading.Thread(target = queue_main, args=(mpd,), daemon=True)

	mpd_thread.start()
	while True:
		try:
			if mpd_songs_remaining(mpd) + len(similar_queue) < THRESHOLD:
				queue_similar(mpd, spotify)
			else:
				mpd.idle('playlist', 'player')
		except KeyboardInterrupt:
			break

	shutdown(mpd)
	print('')

if __name__ == '__main__':
	main()