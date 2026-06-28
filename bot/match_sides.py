from __future__ import annotations



from matchzy import ActiveMatch





def side_channel_names(match: ActiveMatch) -> tuple[str, str]:

    if match.team1_side == "ct":

        ct_name = "Team Alpha (CT)"

        t_name = "Team Bravo (T)"

    else:

        ct_name = "Team Bravo (CT)"

        t_name = "Team Alpha (T)"



    short_id = match.match_id

    return (

        f"Match {short_id} » {ct_name}",

        f"Match {short_id} » {t_name}",

    )





def player_side_channel_id(

    match: ActiveMatch,

    discord_id: int,

    ct_channel_id: int,

    t_channel_id: int,

) -> int | None:

    on_team1 = any(player.discord_id == discord_id for player in match.team1)

    on_team2 = any(player.discord_id == discord_id for player in match.team2)

    if not on_team1 and not on_team2:

        return None



    if match.team1_side == "ct":

        return ct_channel_id if on_team1 else t_channel_id

    return t_channel_id if on_team1 else ct_channel_id


