from typing import Protocol


class LegionVerificationProvider(Protocol):
    async def verify_membership(
        self,
        player_name: str,
        legion_name: str,
    ) -> bool:
        """Return verified membership only through an authorized official source."""


class NoVerificationProvider:
    async def verify_membership(
        self,
        player_name: str,
        legion_name: str,
    ) -> bool:
        raise NotImplementedError(
            "Verified Legion membership is unavailable. "
            "Use self-selected Legion roles until an official authorized API exists."
        )