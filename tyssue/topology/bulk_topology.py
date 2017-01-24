import logging
import itertools
import numpy as np, pandas as pd
from itertools import combinations
from .sheet_topology import face_division
from .base_topology import add_vert
from ..geometry.utils import rotation_matrix

logger = logging.getLogger(name=__name__)


def get_division_edges(eptm, mother,
                       plane_normal,
                       plane_center=None):

    plane_normal = np.asarray(plane_normal)
    if plane_center is None:
        plane_center = eptm.cell_df.loc[mother, eptm.coords]

    n_xy = np.linalg.norm(plane_normal[:2])
    theta = -np.arctan2(n_xy, plane_normal[2])
    direction = [plane_normal[1], -plane_normal[0], 0]
    rot = rotation_matrix(theta, direction)
    cell_verts = set(eptm.edge_df[eptm.edge_df['cell'] == mother]['srce'])
    vert_pos = eptm.vert_df.loc[cell_verts, eptm.coords]
    for c in eptm.coords:
        vert_pos[c] -= plane_center[c]
    vert_pos[:] = np.dot(vert_pos, rot)

    mother_edges = eptm.edge_df[eptm.edge_df['cell'] == mother]
    srce_z = vert_pos.loc[mother_edges['srce'], 'z']
    srce_z.index = mother_edges.index
    trgt_z = vert_pos.loc[mother_edges['trgt'], 'z']
    trgt_z.index = mother_edges.index
    division_edges = mother_edges[((srce_z < 0) &
                                   (trgt_z >= 0))]

    # Order the returned edges so that their centers
    # are oriented counterclockwize in the division plane
    # in preparation for septum creation
    srce_pos = vert_pos.loc[division_edges['srce'],
                            eptm.coords].values
    trgt_pos = vert_pos.loc[division_edges['trgt'],
                            eptm.coords].values
    centers = (srce_pos + trgt_pos)/2
    theta = np.arctan2(centers[:, 2], centers[:, 1])
    return division_edges.index[np.argsort(theta)]


def get_division_vertices(eptm,
                          division_edges=None,
                          mother=None,
                          plane_normal=None,
                          plane_center=None):

    if division_edges is None:
        division_edges = get_division_edges(eptm, mother,
                                            plane_normal,
                                            plane_center)
    vertices = []
    for edge in division_edges:
        new_vert, *new_edges = add_vert(eptm, edge)
        vertices.append(new_vert)
    return vertices


def cell_division(eptm, mother, geom, vertices):

    cell_cols = eptm.cell_df.loc[mother]
    eptm.cell_df = eptm.cell_df.append(cell_cols,
                                       ignore_index=True)
    eptm.cell_df.index.name = 'cell'
    daughter = eptm.cell_df.index[-1]

    pairs = set([frozenset([v1, v2]) for v1, v2
                 in itertools.product(vertices,
                                      vertices) if v1 != v2])
    daughter_faces = []

    # devide existing faces
    for v1, v2 in pairs:
        v1_faces = eptm.edge_df[eptm.edge_df['srce'] == v1]['face']
        v2_faces = eptm.edge_df[eptm.edge_df['srce'] == v2]['face']
        # we should devide a face if both v1 and v2
        # are part of it
        faces = set(v1_faces).intersection(v2_faces)
        for face in faces:
            daughter_faces.append(
                face_division(eptm, face, v1, v2))
    # septum
    face_cols = eptm.face_df.iloc[-2:]
    eptm.face_df = eptm.face_df.append(face_cols, ignore_index=True)
    eptm.face_df.index.name = 'face'
    septum = eptm.face_df.index[-2:]
    daughter_faces.extend(list(septum))

    num_v = len(vertices)
    num_new_edges = num_v*2

    edge_cols = eptm.edge_df.iloc[-num_new_edges:]
    eptm.edge_df = eptm.edge_df.append(edge_cols,
                                       ignore_index=True)
    eptm.edge_df.index.name = 'edge'
    new_edges = eptm.edge_df.index[-num_new_edges:]

    # To keep mother orientation, the first septum face
    # belongs to mother
    for v1, v2, edge, oppo in zip(vertices,
                                  np.roll(vertices, -1),
                                  new_edges[:num_v],
                                  new_edges[num_v:]):
        # Mother septum
        eptm.edge_df.loc[edge,
                         ['srce', 'trgt',
                          'face', 'cell']] = (v1, v2,
                                              septum[0], mother)
        # Daughter septum
        eptm.edge_df.loc[oppo,
                         ['srce', 'trgt',
                          'face', 'cell']] = (v2, v1,
                                              septum[1], daughter)

    eptm.reset_index()
    eptm.reset_topo()
    geom.update_all(eptm)

    m_septum_edges = eptm.edge_df[eptm.edge_df['face'] == septum[0]]
    m_septum_norm = m_septum_edges[eptm.ncoords].mean()
    m_septum_pos = eptm.face_df.loc[septum[0], eptm.coords]

    # splitting the faces between mother and daughter
    # based on the orientation of the vector from septum
    # center to each face center w/r to the septum norm
    mother_faces = set(eptm.edge_df[eptm.edge_df['cell'] == mother]['face'])
    for face in mother_faces:
        if face == septum[0]:
            continue
        dr = eptm.face_df.loc[face, eptm.coords] - m_septum_pos
        proj = (dr.values * m_septum_norm).sum(axis=0)
        f_edges = eptm.edge_df[eptm.edge_df['face'] == face].index
        if proj < 0:
            eptm.edge_df.loc[f_edges, 'cell'] = mother
        else:
            eptm.edge_df.loc[f_edges, 'cell'] = daughter
    eptm.reset_index()
    eptm.reset_topo()
    return daughter


def opposite_face(eptm, fa, face_srce_orbit=None):
    if face_srce_orbit is None:
        face_srce_orbit = eptm.get_orbits(
            'face', 'srce').groupby(level='face').apply(set)
    face_pair = face_srce_orbit[face_srce_orbit ==
                                face_srce_orbit[fa]].index
    if len(face_pair) == 2:
        opp_face = face_pair[face_pair != fa][0]
    elif len(face_pair) == 1:
        opp_face = -1
    else:
        raise ValueError('Invalid topology,'
                         ' incorrect faces: {}'.format(face_pair))
    return opp_face


def IH_transition(eptm, e_1011):
    """
    I → H transition as defined in Okuda et al. 2013
    (DOI 10.1007/s10237-012-0430-7).

    See tyssue/doc/illus/IH_transition.png for the definition of the
    edges, which follow the one in the above article
    """

    v10, v11 = eptm.edge_df.loc[e_1011, ['srce', 'trgt']]
    v_pairs = _get_vertex_pairs_IH(eptm, e_1011)
    try:
        (v1, v4), (v2, v5), (v3, v6) = v_pairs
    except ValueError:
        print('Edge {} is not a valid junction to'
              ' perform IH transition on, aborting'.format(e_1011))
        return
    new_vs = eptm.vert_df.loc[[v1, v2, v3]].copy()
    eptm.vert_df = eptm.vert_df.append(new_vs, ignore_index=True)
    v7, v8, v9 = eptm.vert_df.index[-3:]

    cells = []
    srce_cell_orbits = eptm.get_orbits('srce', 'cell')
    for vi, vj, vk in [(v1, v2, v3),
                       (v4, v5, v6),
                       (v1, v2, v11),
                       (v2, v3, v11),
                       (v3, v1, v11)]:
        cell = list(set(srce_cell_orbits.loc[vi])
                    .intersection(srce_cell_orbits.loc[vj])
                    .intersection(srce_cell_orbits.loc[vk]))
        cells.append(cell[0] if cell else None)

    cA, cB, cC, cD, cE = cells
    if cA is not None:
        # orient vertices 1,2,3 positively
        r_12 = (eptm.vert_df.loc[v2, eptm.coords].values -
                eptm.vert_df.loc[v1, eptm.coords].values).astype(np.float)
        r_23 = (eptm.vert_df.loc[v3, eptm.coords].values -
                eptm.vert_df.loc[v2, eptm.coords].values).astype(np.float)
        r_123 = eptm.vert_df.loc[[v1, v2, v3], eptm.coords].mean(axis=0).values
        r_A = eptm.cell_df.loc[cA, eptm.coords].values
        orient = np.dot(np.cross(r_12, r_23), (r_123 - r_A))
    elif cB is not None:
        # orient vertices 4,5,6 negatively
        r_45 = (eptm.vert_df.loc[v5, eptm.coords].values -
                eptm.vert_df.loc[v4, eptm.coords].values).astype(np.float)
        r_56 = (eptm.vert_df.loc[v6, eptm.coords].values -
                eptm.vert_df.loc[v5, eptm.coords].values).astype(np.float)
        r_456 = eptm.vert_df.loc[[v4, v5, v6], eptm.coords].mean(axis=0).values
        r_B = eptm.cell_df.loc[cB, eptm.coords].values
        orient = -np.dot(np.cross(r_45, r_56), (r_456 - r_B))
    else:
        print('I - H transition is not possible without cells on either ends'
              'of the edge - would result in a hole')
    if orient < 0:
        v1, v2, v3 = v1, v3, v2
        v4, v5, v6 = v4, v6, v5
        cC, cE = cE, cC
    vertices = [v1, v2, v3, v4, v5, v6,
                v7, v8, v9, v10, v11]

    for i, va, vb, new in zip(range(3),
                              (v1, v2, v3),
                              (v4, v5, v6),
                              (v7, v8, v9)):
        # assign v1 -> v10 edges to  v1 -> v7
        e_a10s = eptm.edge_df[(eptm.edge_df['srce'] == va) &
                              (eptm.edge_df['trgt'] == v10)].index
        eptm.edge_df.loc[e_a10s, 'trgt'] = new
        # assign v10 -> v1 edges to  v7 -> v1
        e_10as = eptm.edge_df[(eptm.edge_df['srce'] == v10) &
                              (eptm.edge_df['trgt'] == va)].index
        eptm.edge_df.loc[e_10as, 'srce'] = new
        # assign v4 -> v11 edges to  v4 -> v7
        e_b11s = eptm.edge_df[(eptm.edge_df['srce'] == vb) &
                              (eptm.edge_df['trgt'] == v11)].index
        eptm.edge_df.loc[e_b11s, 'trgt'] = new
        # assign v11 -> v4 edges to  v7 -> v4
        e_11bs = eptm.edge_df[(eptm.edge_df['srce'] == v11) &
                              (eptm.edge_df['trgt'] == vb)].index
        eptm.edge_df.loc[e_11bs, 'srce'] = new

    _set_new_pos_IH(eptm, e_1011, vertices)

    face = eptm.edge_df.loc[e_1011, 'face']
    new_fs = eptm.face_df.loc[[face, face]].copy()
    eptm.face_df = eptm.face_df.append(new_fs,
                                       ignore_index=True)
    fa, fb = eptm.face_df.index[-2:]
    edges_fa_fb = eptm.edge_df.loc[[e_1011, ]*6].copy()
    eptm.edge_df = eptm.edge_df.append(edges_fa_fb,
                                       ignore_index=True)
    new_es = eptm.edge_df.index[-6:]
    for eA, eB, (vi, vj) in zip(new_es[::2], new_es[1::2],
                                [(v7, v8), (v8, v9), (v9, v7)]):
        eptm.edge_df.loc[eA, ['srce', 'trgt', 'face', 'cell']] = vi, vj, fa, cA
        eptm.edge_df.loc[eB, ['srce', 'trgt', 'face', 'cell']] = vj, vi, fb, cB

    # Hardcoding this, as I don't see a clever way around
    news = [(cA, v1, v2, v8, v7),
            (cA, v1, v3, v7, v9),
            (cA, v2, v3, v9, v8),
            (cB, v4, v5, v7, v8),
            (cB, v5, v6, v8, v9),
            (cB, v6, v4, v9, v7),
            (cC, v1, v2, v7, v8),
            (cC, v4, v5, v8, v7),
            (cD, v2, v3, v8, v9),
            (cD, v5, v6, v9, v8),
            (cE, v1, v3, v9, v7),
            (cE, v4, v6, v7, v9)]
    for args in news:
        if args[0] is None:
            continue
        _add_edge_to_existing(eptm, *args)

    # Removing the remaining edges and vertices
    todel_edges = eptm.edge_df[(eptm.edge_df['srce'] == v10) |
                               (eptm.edge_df['srce'] == v10) |
                               (eptm.edge_df['srce'] == v11) |
                               (eptm.edge_df['srce'] == v11)].index

    eptm.edge_df = eptm.edge_df.loc[eptm.edge_df.index.delete(todel_edges)]
    eptm.vert_df = eptm.vert_df.loc[eptm.vert_df.index.delete([v10, v11])]
    eptm.edge_df.index.name = 'edge'
    eptm.reset_index()
    eptm.reset_topo()


def HI_transition(eptm, face):
    """
    H → I transition as defined in Okuda et al. 2013
    (DOI 10.1007/s10237-012-0430-7).

    See tyssue/doc/illus/IH_transition.png for the definition of the
    edges, which follow the one in the above article
    """
    if eptm.face_df.loc[face, 'num_sides'] != 3:
        raise ValueError('Only three sided faces can undergo a H-I transition')

    fa = face
    f_edges = eptm.edge_df[eptm.edge_df['face'] == fa]
    v7 = f_edges.iloc[0]['srce']
    v8 = f_edges.iloc[0]['trgt']
    v9 = f_edges[f_edges['srce'] == v8]['trgt'].iloc[0]

    cA = f_edges['cell'].iloc[0]
    face_srce_orbit = eptm.get_orbits(
        'face', 'srce').groupby(level='face').apply(set)
    fb = opposite_face(eptm, fa, face_srce_orbit)
    cB = eptm.edge_df[eptm.edge_df['face'] == fb]['cell'].iloc[0]
    cA_edges = eptm.edge_df[eptm.edge_df['cell'] == cA]
    cB_edges = eptm.edge_df[eptm.edge_df['cell'] == cB]

    eptm.vert_df = eptm.vert_df.append(eptm.vert_df.loc[[v8, v9]],
                                       ignore_index=True)

    eptm.vert_df.index.name = 'vert'
    v10, v11 = eptm.vert_df.index[-2:]
    _set_new_pos_HI(eptm, fa, fb, v10, v11)

    v_pairs = []
    for vk in (v7, v8, v9):
        vi = cA_edges[cA_edges['srce'] == vk]['trgt'].iloc[0]
        vj = cB_edges[cB_edges['srce'] == vk]['trgt'].iloc[0]
        v_pairs.append((vi, vj))

    (v1, v4), (v2, v5), (v3, v6) = v_pairs

    srce_cell_orbit = eptm.get_orbits('srce', 'cell')
    cells = [cA, cB, ]
    for (vi, vj, vk) in [(v1, v2, v4),
                         (v2, v3, v5),
                         (v1, v3, v4)]:
        cell = list(set(srce_cell_orbit.loc[vi])
                    .intersection(srce_cell_orbit.loc[vj])
                    .intersection(srce_cell_orbit.loc[vk]))

        cells.append(cell[0] if cell else None)
    cA, cB, cC, cD, cE = cells

    for vi, vj, vk in zip((v1, v2, v3),
                          (v4, v5, v6),
                          (v7, v8, v9)):
        e_iks = eptm.edge_df[(eptm.edge_df['srce'] == vi) &
                             (eptm.edge_df['trgt'] == vk)].index
        eptm.edge_df.loc[e_iks, 'trgt'] = v10

        e_kis = eptm.edge_df[(eptm.edge_df['srce'] == vk) &
                             (eptm.edge_df['trgt'] == vi)].index
        eptm.edge_df.loc[e_kis, 'srce'] = v10

        e_jks = eptm.edge_df[(eptm.edge_df['srce'] == vj) &
                             (eptm.edge_df['trgt'] == vk)].index
        eptm.edge_df.loc[e_jks, 'trgt'] = v11

        e_kjs = eptm.edge_df[(eptm.edge_df['srce'] == vk) &
                             (eptm.edge_df['trgt'] == vj)].index
        eptm.edge_df.loc[e_kjs, 'srce'] = v11

    # Closing the faces with v10 → v11 edges
    news = [(cC, v2, v5, v10, v11),
            (cC, v1, v4, v11, v10),
            (cD, v2, v5, v11, v10),
            (cD, v3, v6, v10, v11),
            (cE, v3, v6, v11, v10),
            (cE, v1, v4, v10, v11)]
    for args in news:
        if args[0] is None:
            continue
        _add_edge_to_existing(eptm, *args)

    # Removing the remaining edges and vertices
    todel_edges = eptm.edge_df[(eptm.edge_df['srce'] == v7) |
                               (eptm.edge_df['trgt'] == v7) |
                               (eptm.edge_df['srce'] == v8) |
                               (eptm.edge_df['trgt'] == v8) |
                               (eptm.edge_df['srce'] == v9) |
                               (eptm.edge_df['trgt'] == v9)].index

    eptm.edge_df = eptm.edge_df.loc[eptm.edge_df.index.delete(todel_edges)]
    eptm.vert_df = eptm.vert_df.loc[eptm.vert_df.index.delete([v7, v8, v9])]
    eptm.face_df = eptm.face_df.loc[eptm.face_df.index.delete([fa, fb])]
    eptm.edge_df.index.name = 'edge'

    eptm.reset_index()
    eptm.reset_topo()


def _add_edge_to_existing(eptm, cell,
                          vi, vj,
                          new_srce, new_trgt):
    """
    Add edges between vertices v7, v8 and v9 to the existing faces
    """
    cell_edges = eptm.edge_df[eptm.edge_df['cell'] == cell]
    for f, data in cell_edges.groupby('face'):
        if {vi, vj, new_srce, new_trgt}.issubset(
                set(data['srce']).union(data['trgt'])):
            good_f = f
            break
    else:
        raise ValueError('no face with vertices {}, {}, {} and {}'
                         ' was found for cell {}'
                         .format(vi, vj, new_srce, new_trgt, cell))
    eptm.edge_df = eptm.edge_df.append(cell_edges.iloc[-1],
                                       ignore_index=True)
    new_e = eptm.edge_df.index[-1]
    eptm.edge_df.loc[new_e, ['srce', 'trgt',
                             'face', 'cell']] = (new_srce, new_trgt,
                                                 good_f, cell)


def _set_new_pos_IH(eptm, e_1011, vertices):
    '''Okuda 2013 equations 46 to 56

    '''
    Dl_th = eptm.settings['threshold_length']

    (v1, v2, v3, v4, v5, v6,
     v7, v8, v9, v10, v11) = vertices

    # eq. 49
    r_1011 = - eptm.edge_df.loc[e_1011, eptm.dcoords].values
    u_T = (r_1011 / np.linalg.norm(r_1011))
    # eq. 50
    r0 = eptm.vert_df.loc[[v10, v11], eptm.coords].mean(axis=0).values

    v_0ns = []
    for vi, vj, vk in zip((v1, v2, v3),
                          (v4, v5, v6),
                          (v7, v8, v9)):
        # eq. 54 - 56
        r0i, r0j = (eptm.vert_df.loc[[vi, vj], eptm.coords].values -
                    r0[np.newaxis, :])
        w_0k = (r0i/np.linalg.norm(r0i) + r0j/np.linalg.norm(r0j)) / 2
        # eq. 51 - 53
        v_0k = w_0k - (np.dot(w_0k, u_T)) * u_T
        v_0ns.append(v_0k)

    # see definition of l_max bellow eq. 56
    l_max = np.max([np.linalg.norm(v_n - v_m) for (v_n, v_m)
                    in itertools.combinations(v_0ns, 2)])
    # eq. 46 - 49
    for vk, v_0k in zip((v7, v8, v9), v_0ns):
        eptm.vert_df.loc[vk, eptm.coords] = r0 + (Dl_th / l_max) * v_0k


def _get_vertex_pairs_IH(eptm, e_1011):

    srce_face_orbits = eptm.get_orbits('srce', 'face')
    v10, v11 = eptm.edge_df.loc[e_1011, ['srce', 'trgt']]
    v10_out = set(eptm.edge_df[eptm.edge_df['srce'] == v10]['trgt']) - {v11}
    faces_123 = {v: set(srce_face_orbits.loc[v])
                 for v in v10_out}

    v11_out = set(eptm.edge_df[eptm.edge_df['srce'] == v11]['trgt']) - {v10}
    faces_456 = {v: set(srce_face_orbits.loc[v])
                 for v in v11_out}
    v_pairs = []
    for vi in v10_out:
        for vj in v11_out:
            common_face = faces_123[vi].intersection(faces_456[vj])
            if common_face:
                v_pairs.append((vi, vj))
                break
        else:
            raise ValueError('No corresponding vertex'
                             ' for vertex {}'.format(vi))
    return v_pairs


def _set_new_pos_HI(eptm, fa, fb, v10, v11):

    r0 = eptm.face_df.loc[fa, eptm.coords].values
    norm_a = eptm.edge_df[eptm.edge_df['face'] == fa][
        eptm.ncoords].mean(axis=0).values
    norm_b = eptm.edge_df[eptm.edge_df['face'] == fb][
        eptm.ncoords].mean(axis=0).values
    norm_a = norm_a / np.linalg.norm(norm_a)
    norm_b = norm_b / np.linalg.norm(norm_b)
    Dl_th = eptm.settings['threshold_length']
    eptm.vert_df.loc[v10, eptm.coords] = r0 + Dl_th/2 * norm_b
    eptm.vert_df.loc[v11, eptm.coords] = r0 + Dl_th/2 * norm_a


def condition_4i(eptm):
    """
    Return an index over the faces violating condition 4 i in Okuda et al 2013,
    that is edges (from the same face) sharing two vertices simultaneously.
    """
    num_srces = eptm.edge_df.groupby('face')['srce'].apply(lambda s: len(set(s)))
    num_sides = eptm.face_df['num_sides']
    return eptm.face_df[num_srces != num_sides].index


def get_neighbour_face_pairs(eptm):
    """
    Returns a pandas Series of neighboring face pairs (as forzen sets of 2 indexes)
    """
    pairs = []
    eptm.edge_df['v_pair'] =  eptm.edge_df[['srce', 'trgt']].apply(frozenset, axis=1)

    _ = eptm.edge_df.groupby('v_pair')['face'].apply(
        lambda s: pairs.extend([frozenset((a, b)) for a, b in combinations(s.values, 2)]))
    return pd.Series(pairs).drop_duplicates()

def get_num_common_edges(eptm):
    """
    Returns the number of common edges between two neighboring faces
    this number is set to -1 if those faces are opposite and share the
    same edges.
    """
    pairs = get_neighbour_face_pairs(eptm)
    face_v_pair_orbit = eptm.edge_df.groupby('face').apply(
        lambda df: frozenset(df['v_pair']))
    n_common = [
        len(face_v_pair_orbit.loc[fa].intersection(face_v_pair_orbit.loc[fb]))
        if face_v_pair_orbit.loc[fb] != face_v_pair_orbit.loc[fa]
        else -1 for fa, fb in pairs]
    n_common = pd.Series(n_common, index=pd.Index(pairs, name='face_pairs'))
    return n_common

def condition_4ii(eptm):
    """
    Return a list of face pairs sharing more than one edge, as defined
    in Okuda et al. 2013 condition 4 ii
    """
    n_common = get_num_common_edges(eptm)
    return list(n_common[n_common > 2].index)
