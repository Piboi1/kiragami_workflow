"""Builds SimScale SDK model objects for one design (no network calls).

Mirrors the manual Kiragami_CFD "Incompressible" setup:
  * k-omega SST, steady state, 500 iterations, dt 1, write every 10
  * Air (assigned separately via the materials API)
  * Wall 3: slip on the four side faces
  * Velocity inlet 2: Max Z, (0, 0, Uz), Uz < 0 (flow travels in -Z)
  * Pressure outlet 3: Min Z, 0 Pa gauge
  * canopy faces left unassigned -> default no-slip wall
  * force & moment coefficients on the canopy faces
  * Simmetrix standard mesher, automatic sizing, fineness 4.5, automatic
    boundary layers, physics-based meshing, hex core
"""

from simscale_sdk import (
    AdvancedConcepts,
    AutomaticCurvature,
    AutomaticLayerOff,
    AutomaticLayerOn,
    AutomaticMeshSizingSimmetrix,
    ComponentVectorFunction,
    ConstantFunction,
    DecimalVector,
    DimensionalArea,
    DimensionalFunctionPressure,
    DimensionalLength,
    DimensionalPressure,
    DimensionalSpeed,
    DimensionalTime,
    DimensionalVectorFunctionSpeed,
    DimensionalVectorLength,
    FixedValuePBC,
    FixedValueVBC,
    FluidInitialConditions,
    FluidModel,
    FluidNumerics,
    FluidResultControls,
    FluidSimulationControl,
    ForceMomentCoefficientsResultControl,
    Incompressible,
    IncompressibleFluidMaterials,
    PressureOutletBC,
    RelaxationFactor,
    ResidualControls,
    ScotchDecomposeAlgorithm,
    SimmetrixMeshingFluid,
    SimulationSpec,
    SlipVBC,
    StationaryTimeDependency,
    TimeStepWriteControl,
    Tolerance,
    TopologicalReference,
    VelocityInletBC,
    WallBC,
)


def _vec(x, y, z, unit="m"):
    return DimensionalVectorLength(value=DecimalVector(x=x, y=y, z=z), unit=unit)


def build_model(sim_cfg, entities, uz_mps, reference_area_in2, reference_length_in):
    """Incompressible model for one design.

    entities: dict face-group -> list of SimScale internal entity names, with
        keys inlet, outlet, side_xmin, side_xmax, side_ymin, side_ymax, canopy.
    """

    sides = entities["side_xmin"] + entities["side_xmax"] + entities["side_ymin"] + entities["side_ymax"]
    speed = abs(uz_mps)

    return Incompressible(
        turbulence_model=sim_cfg["TURBULENCE_MODEL"],
        time_dependency=StationaryTimeDependency(),
        model=FluidModel(),
        materials=IncompressibleFluidMaterials(),
        initial_conditions=FluidInitialConditions(),
        advanced_concepts=AdvancedConcepts(),
        numerics=FluidNumerics(
            relaxation_factor=RelaxationFactor(),
            num_non_orthogonal_correctors=sim_cfg["NON_ORTHOGONAL_CORRECTORS"],
            pressure_reference_cell=0,
            pressure_reference_value=DimensionalPressure(value=0, unit="Pa"),
            residual_controls=ResidualControls(
                velocity=Tolerance(),
                pressure=Tolerance(),
                turbulent_kinetic_energy=Tolerance(),
                omega_dissipation_rate=Tolerance(),
            ),
        ),
        boundary_conditions=[
            WallBC(
                name="Wall 3",
                velocity=SlipVBC(),
                topological_reference=TopologicalReference(entities=sides),
            ),
            VelocityInletBC(
                name="Velocity inlet 2",
                velocity=FixedValueVBC(
                    value=DimensionalVectorFunctionSpeed(
                        value=ComponentVectorFunction(
                            x=ConstantFunction(value=0),
                            y=ConstantFunction(value=0),
                            z=ConstantFunction(value=uz_mps),
                        ),
                        unit="m/s",
                    )
                ),
                topological_reference=TopologicalReference(entities=entities["inlet"]),
            ),
            PressureOutletBC(
                name="Pressure outlet 3",
                gauge_pressure=FixedValuePBC(
                    value=DimensionalFunctionPressure(value=ConstantFunction(value=0), unit="Pa")
                ),
                topological_reference=TopologicalReference(entities=entities["outlet"]),
            ),
        ],
        simulation_control=FluidSimulationControl(
            end_time=DimensionalTime(value=sim_cfg["END_TIME_S"], unit="s"),
            delta_t=DimensionalTime(value=sim_cfg["DELTA_T_S"], unit="s"),
            write_control=TimeStepWriteControl(write_interval=sim_cfg["WRITE_INTERVAL"]),
            max_run_time=DimensionalTime(value=sim_cfg["MAX_RUN_TIME_S"], unit="s"),
            decompose_algorithm=ScotchDecomposeAlgorithm(),
        ),
        result_control=FluidResultControls(
            forces_moments=[
                ForceMomentCoefficientsResultControl(
                    name="Force coefficients",
                    center_of_rotation=_vec(0, 0, 0),
                    # Drag is measured along the flow (-Z); lift along +X.
                    drag_direction=_vec(0, 0, -1),
                    lift_direction=_vec(1, 0, 0),
                    pitch_axis=_vec(0, 1, 0),
                    freestream_velocity_magnitude=DimensionalSpeed(value=speed, unit="m/s"),
                    reference_length=DimensionalLength(value=reference_length_in, unit="in"),
                    reference_area_value=DimensionalArea(value=reference_area_in2, unit="in²"),
                    write_control=TimeStepWriteControl(write_interval=1),
                    topological_reference=TopologicalReference(entities=entities["canopy"]),
                )
            ]
        ),
    )


def build_simulation_spec(name, geometry_id, model):
    return SimulationSpec(name=name, geometry_id=geometry_id, model=model)


def build_mesh_model(sim_cfg):
    return SimmetrixMeshingFluid(
        sizing=AutomaticMeshSizingSimmetrix(fineness=sim_cfg["MESH_FINENESS"], curvature=AutomaticCurvature()),
        automatic_layer_settings=AutomaticLayerOn() if sim_cfg["MESH_BOUNDARY_LAYERS"] else AutomaticLayerOff(),
        physics_based_meshing=sim_cfg["MESH_PHYSICS_BASED"],
        hex_core=sim_cfg["MESH_HEX_CORE"],
        max_meshing_run_time=DimensionalTime(value=sim_cfg["MESH_MAX_RUN_TIME_S"], unit="s"),
    )
