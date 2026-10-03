"""Challenger matches that are in a set are live even when ESPN omits them."""

from app.services.tennis.livescore import parse_livescore

_PAGE = """
<div class="header-cg-1 cg_head_tr match_table_header"><a href="/done"><h2>France - Challenger</h2></a></div>
<div id="10" class="cg-matches-1 match_table_content tennis_score_grid">
  <div class="tennis_status"><samp>RES</samp></div>
  <a href="/done-match">
    <span class="tennis_home_player">Finished, Player</span>
    <span class="tennis_away_player">Other, Player</span>
  </a>
</div>
<div id="11" class="cg-matches-1 match_table_content tennis_score_grid">
  <div class="tennis_status"><strong>Set 2</strong><i class="live_icon"><img src="/live.gif" /></i></div>
  <a href="/France-Chidekh-3383231" class="matches_grid_anchor">
    <span class="tennis_home_player">Chidekh, Clement</span>
    <span class="tennis_away_player">Bernet, Henry</span>
    <span class="tennis_home_ball">&nbsp;</span>
    <span class="tennis_away_ball"><img src="/ball.png"></span>
    <span class="tennis_home_set_design tennis_set_design_first">6</span>
    <span class="tennis_away_set_design tennis_set_design_first">2</span>
  </a>
</div>
"""


def test_only_the_live_row_is_kept_and_names_are_readable():
    found = parse_livescore(_PAGE)
    assert len(found) == 1
    match = found[0]
    assert match.player_a == "Clement Chidekh"
    assert match.player_b == "Henry Bernet"
    assert match.sets == [(6, 2)]
    assert match.detail == "Set 2"
    assert match.server_name == "Henry Bernet"
    assert match.source == "livescore"
    assert match.tournament == "France - Challenger"
    assert match.source_url.endswith("/France-Chidekh-3383231")
