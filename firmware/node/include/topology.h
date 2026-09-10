#ifndef TOPOLOGY_H
#define TOPOLOGY_H

#define NUM_NODES 6
#define INF 99999.0f

#define ID_EX1 0
#define ID_N1  1
#define ID_N2  2
#define ID_N3  3
#define ID_N4  4
#define ID_EX2 5

const float BASE_GRAPH[NUM_NODES][NUM_NODES] = {
    // EX1    N1     N2     N3     N4     EX2
    {  0.0f,  2.0f,  INF,   INF,   INF,   INF  }, // EX1
    {  2.0f,  0.0f,  4.5f,  INF,   INF,   INF  }, // N1
    {  INF,   4.5f,  0.0f,  5.0f,  3.5f,  INF  }, // N2
    {  INF,   INF,   5.0f,  0.0f,  INF,   INF  }, // N3
    {  INF,   INF,   3.5f,  INF,   0.0f,  2.5f }, // N4
    {  INF,   INF,   INF,   INF,   2.5f,  0.0f }  // EX2
};

#endif
