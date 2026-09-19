import aiohttp


BUNGIE_BASE = "https://www.bungie.net/Platform"


def normalize_bungie_image_url(path_value):
    if not path_value:
        return None
    if path_value.startswith("http://") or path_value.startswith("https://"):
        return path_value
    return f"https://www.bungie.net{path_value}"


class BungieAPI:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = aiohttp.ClientSession(headers={"X-API-Key": self.api_key})

    async def search_global_name(self, global_name: str):
        """
        Search for a Bungie user by global name, for example WH33LS#8650.
        Tries both raw and zero-padded displayNameCode.
        Returns a list of membership info dicts.
        """
        if "#" in global_name:
            name, code = global_name.split("#", 1)
            code = code.strip()
        else:
            name, code = global_name, ""

        path = "/Destiny2/SearchDestinyPlayerByBungieName/AllPlatforms/"
        results = []

        if code.isdigit():
            body = {"displayName": name, "displayNameCode": int(code)}
            async with self.session.post(f"{BUNGIE_BASE}{path}", json=body) as response:
                data = await response.json()
                results = data.get("Response", [])
            if results:
                return results

            if len(code) < 4:
                padded_code = code.zfill(4)
                body = {"displayName": name, "displayNameCode": int(padded_code)}
                async with self.session.post(f"{BUNGIE_BASE}{path}", json=body) as response:
                    data = await response.json()
                    results = data.get("Response", [])
                if results:
                    return results

        body = {"displayName": name, "displayNameCode": 0}
        async with self.session.post(f"{BUNGIE_BASE}{path}", json=body) as response:
            data = await response.json()
            results = data.get("Response", [])
        return results

    async def _get(self, path: str, params=None):
        async with self.session.get(f"{BUNGIE_BASE}{path}", params=params) as response:
            data = await response.json()
            return data.get("Response", {})

    async def get_activity_history(
        self,
        membership_type,
        membership_id,
        character_id,
        mode=82,
        count=100,
        page=0,
    ):
        path = (
            f"/Destiny2/{membership_type}/Account/{membership_id}"
            f"/Character/{character_id}/Stats/Activities/"
        )
        params = {"count": count, "page": page}
        if mode is not None:
            params["mode"] = mode
        return await self._get(path, params=params)

    async def get_pgcr(self, instance_id):
        path = f"/Destiny2/Stats/PostGameCarnageReport/{instance_id}/"
        return await self._get(path)

    async def get_profile(self, membership_type, membership_id):
        path = f"/Destiny2/{membership_type}/Profile/{membership_id}/"
        # Include both profile user info (100) and characters (200).
        # User info is needed for reliable Bungie display-name rendering.
        params = {"components": "100,200"}
        return await self._get(path, params=params)

    async def get_guardian_ranks(self, membership_type, membership_id):
        """Fetch component 100 (Profiles) which contains guardian rank fields."""
        path = f"/Destiny2/{membership_type}/Profile/{membership_id}/"
        params = {"components": "100"}
        return await self._get(path, params=params)

    async def get_guardian_rank_definition(self, rank_number):
        """Fetch a Guardian Rank manifest definition (1-11) including symbol artwork path."""
        path = f"/Destiny2/Manifest/DestinyGuardianRankDefinition/{int(rank_number)}/"
        return await self._get(path)

    async def get_activity_definition(self, activity_hash):
        path = f"/Destiny2/Manifest/DestinyActivityDefinition/{activity_hash}/"
        return await self._get(path)

    async def get_activity_modifier_definition(self, modifier_hash):
        path = f"/Destiny2/Manifest/DestinyActivityModifierDefinition/{modifier_hash}/"
        return await self._get(path)

    async def get_objective_definition(self, objective_hash):
        path = f"/Destiny2/Manifest/DestinyObjectiveDefinition/{objective_hash}/"
        return await self._get(path)

    async def get_milestones(self):
        path = "/Destiny2/Milestones/"
        return await self._get(path)

    async def get_milestone_definition(self, milestone_hash):
        path = f"/Destiny2/Manifest/DestinyMilestoneDefinition/{milestone_hash}/"
        return await self._get(path)

    async def get_clan_roster(self, group_id):
        """
        Fetch clan roster members for a clan group ID.
        Returns dicts with displayName, membershipId, membershipType, and avatarUrl.
        """
        path = f"/GroupV2/{group_id}/Members/"
        data = await self._get(path)

        members = []
        for member in data.get("results", []):
            info = member.get("destinyUserInfo") or member.get("destinyMemberships", [{}])[0]
            if not info:
                continue
            members.append(
                {
                    "displayName": info.get("displayName"),
                    "membershipId": info.get("membershipId"),
                    "membershipType": info.get("membershipType"),
                    "avatarUrl": normalize_bungie_image_url(
                        (member.get("bungieNetUserInfo") or {}).get("iconPath")
                        or (member.get("bungieNetUserInfo") or {}).get("profilePicturePath")
                        or info.get("profilePicturePath")
                        or info.get("iconPath")
                    ),
                }
            )
        return members

    async def close(self):
        await self.session.close()
