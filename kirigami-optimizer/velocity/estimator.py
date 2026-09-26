"""Physics-based inlet velocity (Uz) estimate for each CFD run.

    v_t = sqrt(2 m g / (rho * Cd * A))

m is the total falling mass (payload + canopy), A the reference area used for
Cd (the same projected-disk area that is passed to SimScale's force
coefficients), rho the air density that SimScale's Air uses.

Cd for a design that has not been simulated yet comes from:
  * the seed: Cd back-calculated from your best real drop test, and
  * the CFD Cd of previously simulated designs, weighted by how similar they
    are (Gaussian kernel in the normalized search space), so a close
    neighbour dominates and far-away runs barely count.

Two refinement modes (config VELOCITY["CD_REFINEMENT"]):
  "anchored" (default): CFD informs the *relative* Cd between designs, the
      drop test sets the absolute level:
          Cd = Cd_seed * (kernel-avg CFD Cd near x) / (median CFD Cd)
      Use this when CFD Cd and real-world Cd differ systematically (e.g.
      the physical canopy does not open to the modeled shape).
  "direct": literally blend the seed with neighbouring CFD Cd values:
          Cd = (w0 * Cd_seed + sum w_i Cd_i) / (w0 + sum w_i)
"""

import math

import numpy as np

G = 9.80665
IN2_TO_M2 = 0.0254 ** 2


def terminal_velocity(mass_kg, cd, area_m2, rho):
    """v = sqrt(2 m g / (rho Cd A))  [m/s]."""

    return math.sqrt(2.0 * mass_kg * G / (rho * cd * area_m2))


def cd_from_velocity(mass_kg, velocity, area_m2, rho):
    """Inverse of terminal_velocity: Cd = 2 m g / (rho v^2 A)."""

    return 2.0 * mass_kg * G / (rho * velocity ** 2 * area_m2)


def drop_time(height_m, v_terminal):
    """Fall time from rest through height_m with quadratic drag."""

    # y(t) = (vt^2/g) ln cosh(g t / vt)  ->  t = (vt/g) arccosh(exp(g h / vt^2))
    x = G * height_m / v_terminal ** 2
    if x > 700:  # exp overflow: effectively terminal from the start
        return height_m / v_terminal
    return (v_terminal / G) * math.acosh(math.exp(x))


def terminal_velocity_from_drop(height_m, time_s):
    """Terminal velocity whose fall-from-rest over height_m takes time_s.

    The average speed height/time underestimates v_t because the canopy
    starts from rest; this solves the quadratic-drag equation of motion
    instead. Returns inf if time_s is shorter than a vacuum fall allows.
    """

    if time_s <= math.sqrt(2 * height_m / G):
        return math.inf
    lo, hi = 1e-3, 1e3
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if drop_time(height_m, mid) > time_s:
            lo = mid  # too slow -> v_t must be larger
        else:
            hi = mid
    return 0.5 * (lo + hi)


def total_mass_kg(cfg_velocity, parachute_diameter_in):
    """Payload + canopy mass. Canopy = sheet areal density * flat disk area."""

    v = cfg_velocity
    canopy_g = v.get("CANOPY_MASS_G")
    if canopy_g is None:
        flat_area_m2 = math.pi * (parachute_diameter_in / 2) ** 2 * IN2_TO_M2
        canopy_g = v["SHEET_GSM"] * flat_area_m2
    return (v["PAYLOAD_MASS_G"] + canopy_g) / 1000.0


def seed_cd(cfg_velocity, parachute_diameter_in):
    """Cd of the seed drop test (or the explicit SEED_CD override)."""

    v = cfg_velocity
    if v.get("SEED_CD") is not None:
        return float(v["SEED_CD"]), {"source": "SEED_CD override"}

    test = v["DROP_TEST_SEED"]
    if v.get("DROP_TEST_CORRECT_FOR_ACCELERATION", True):
        speed = terminal_velocity_from_drop(test["drop_height_m"], test["descent_time_s"])
    else:
        speed = test["drop_height_m"] / test["descent_time_s"]
    mass = total_mass_kg(v, parachute_diameter_in)
    area = test["reference_area_in2"] * IN2_TO_M2
    cd = cd_from_velocity(mass, speed, area, v["AIR_DENSITY"])
    return cd, {
        "source": f"drop test {test['name']}",
        "seed_velocity_mps": speed,
        "seed_mass_kg": mass,
    }


class VelocityEstimator:
    """Predicts Uz for a design from the seed Cd and nearby CFD results."""

    def __init__(self, cfg_velocity, parachute_diameter_in):
        self.cfg = cfg_velocity
        self.seed_cd, self.seed_info = seed_cd(cfg_velocity, parachute_diameter_in)
        self.mass_kg = total_mass_kg(cfg_velocity, parachute_diameter_in)

    def estimate_cd(self, x_norm, history):
        """Cd estimate at normalized design x_norm.

        history: list of (x_norm_i, cfd_cd_i, run_id_i) for finished runs.
        """

        mode = self.cfg.get("CD_REFINEMENT", "anchored")
        length = float(self.cfg.get("KERNEL_LENGTH_SCALE", 0.2))
        w0 = float(self.cfg.get("SEED_WEIGHT", 1.0))

        if not history:
            return self.seed_cd, {"cd_source": "seed", "nearest_run": None, "nearest_distance": None}

        x = np.asarray(x_norm, dtype=float)
        xs = np.array([h[0] for h in history], dtype=float)
        cds = np.array([h[1] for h in history], dtype=float)
        d = np.linalg.norm(xs - x, axis=1)
        w = np.exp(-0.5 * (d / length) ** 2)
        i_near = int(np.argmin(d))
        info = {"nearest_run": history[i_near][2], "nearest_distance": float(d[i_near]),
                "kernel_weight_sum": float(w.sum())}

        if mode == "direct":
            cd = (w0 * self.seed_cd + float((w * cds).sum())) / (w0 + float(w.sum()))
            info["cd_source"] = "direct blend of seed + neighbouring CFD"
        elif mode == "anchored":
            median = float(np.median(cds))
            # Local CFD average, shrunk toward the median when no run is close.
            local = (w0 * median + float((w * cds).sum())) / (w0 + float(w.sum()))
            cd = self.seed_cd * local / median
            info["cd_source"] = "seed scaled by neighbouring CFD / median CFD"
            info["cfd_ratio"] = local / median
        else:
            raise ValueError(f"unknown CD_REFINEMENT {mode!r}")
        return cd, info

    def estimate(self, x_norm, history, reference_area_in2):
        """Returns dict with uz_mps (negative: flow travels in -Z) and provenance."""

        cd, info = self.estimate_cd(x_norm, history)
        speed = terminal_velocity(self.mass_kg, cd, reference_area_in2 * IN2_TO_M2, self.cfg["AIR_DENSITY"])
        lo, hi = self.cfg.get("UZ_CLAMP_MPS", (0.5, 30.0))
        clamped = min(max(speed, lo), hi)
        return {
            "predicted_speed_mps": clamped,
            "uz_mps": -clamped,
            "speed_was_clamped": clamped != speed,
            "cd_used_for_velocity": cd,
            "seed_cd": self.seed_cd,
            "mass_kg": self.mass_kg,
            **info,
        }
