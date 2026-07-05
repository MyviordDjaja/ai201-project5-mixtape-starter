"""
tests/test_feed.py - Mixtape

Regression tests for the "Friends Listening Now" feed recency cutoff.
"""

import pytest
from datetime import datetime, timedelta, timezone
from app import create_app, db
from models import User, Song, ListeningEvent, friendships
from services.feed_service import get_friends_listening_now


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


def make_friends(u1, u2):
    db.session.execute(friendships.insert().values(user_id=u1.id, friend_id=u2.id))
    db.session.execute(friendships.insert().values(user_id=u2.id, friend_id=u1.id))


def test_listening_now_only_shows_recent_listens(app):
    """
    A friend who listened minutes ago appears in the feed; a friend whose
    most recent listen is hours old does not. Against the buggy 24-hour
    cutoff, the stale friend appeared too, so this test failed.
    """
    with app.app_context():
        viewer = User(username="viewer", email="viewer@example.com")
        recent_friend = User(username="recent", email="recent@example.com")
        stale_friend = User(username="stale", email="stale@example.com")
        db.session.add_all([viewer, recent_friend, stale_friend])
        db.session.flush()

        make_friends(viewer, recent_friend)
        make_friends(viewer, stale_friend)

        song = Song(title="Test Track", artist="Test Artist", shared_by=viewer.id)
        db.session.add(song)
        db.session.flush()

        now = datetime.now(timezone.utc)
        db.session.add(ListeningEvent(
            user_id=recent_friend.id, song_id=song.id,
            listened_at=now - timedelta(minutes=10),
        ))
        db.session.add(ListeningEvent(
            user_id=stale_friend.id, song_id=song.id,
            listened_at=now - timedelta(hours=3),
        ))
        db.session.commit()

        feed = get_friends_listening_now(viewer.id)
        usernames = [entry["friend"]["username"] for entry in feed]

        assert "recent" in usernames
        assert "stale" not in usernames


def test_listening_now_empty_when_no_recent_listens(app):
    """A user whose only friend listened hours ago sees an empty feed."""
    with app.app_context():
        viewer = User(username="viewer", email="viewer@example.com")
        friend = User(username="friend", email="friend@example.com")
        db.session.add_all([viewer, friend])
        db.session.flush()
        make_friends(viewer, friend)

        song = Song(title="Test Track", artist="Test Artist", shared_by=viewer.id)
        db.session.add(song)
        db.session.flush()

        db.session.add(ListeningEvent(
            user_id=friend.id, song_id=song.id,
            listened_at=datetime.now(timezone.utc) - timedelta(hours=3),
        ))
        db.session.commit()

        assert get_friends_listening_now(viewer.id) == []
